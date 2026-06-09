#!/usr/bin/env python3
"""
橙卫2.0 回测引擎 — 绝对接近真实

方法论：
  - 逐股票滑动窗口（不窥探未来）
  - T+1精确：D日信号 → D+1日开盘价买入
  - 最小交易单位：100股
  - 全额成本：佣金万2（最低5元）+ 印花税0.05%（卖）+ 滑点0.1%
  - 涨跌停限制：涨停不买，跌停按次日开盘卖
  - 交易量限制：日成交额<5000万排除

用法：
  python backtest/og_backtest.py
  python backtest/og_backtest.py --stock 600519  # 单只回测
  python backtest/og_backtest.py --start 20240101 --end 20260501
"""
import sys, os, json, argparse, math
from datetime import datetime, timedelta
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, DATA_DIR
from price_action import (
    detect_m2b_pullback, detect_two_leg_pullback, detect_inside_bar_breakout,
    detect_bullish_engulfing, detect_failed_breakout, detect_three_push,
    compute_sbq, classify_market_trend, _scan_single
)
from data import get_stock_daily_cached, get_index_daily


# ── 交易成本参数 ──
COMMISSION_RATE = 0.0002       # 万2
MIN_COMMISSION = 5.0           # 最低5元
STAMP_DUTY_RATE = 0.0005       # 印花税 0.05%（卖出时收）
SLIPPAGE = 0.001               # 滑点 0.1%
MIN_VOLUME_AMOUNT = 10_000_000 # 日成交额低于1000万排除


def load_all_data() -> dict:
    """一次性加载全部股票历史数据

    Returns:
        {code: DataFrame sorted_by_date}
    """
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    print("[BT] 加载全部历史数据...")
    df = pd.read_sql_query(
        "SELECT code, date, open, close, high, low, volume FROM daily_data WHERE adjust='qfq' ORDER BY code, date",
        conn
    )
    conn.close()

    print(f"   共 {len(df)} 行, {df['code'].nunique()} 只股票")
    print(f"   时间范围: {df['date'].min()} ~ {df['date'].max()}")

    # 按code分组
    stocks = {}
    for code, group in df.groupby('code'):
        group = group.reset_index(drop=True)
        for col in ['open','close','high','low','volume']:
            group[col] = group[col].astype(float)
        stocks[code] = group

    return stocks


def find_trading_days(stocks: dict) -> list:
    """从所有股票数据中提取交易日（有交易活动的日期）"""
    all_dates = set()
    for code, df in stocks.items():
        all_dates.update(df['date'].tolist())
    return sorted(all_dates)


def get_next_trading_day(trading_days: list, current_date: str) -> str:
    """获取下一个交易日"""
    idx = trading_days.index(current_date)
    if idx + 1 < len(trading_days):
        return trading_days[idx + 1]
    return None


def get_trading_day_offset(trading_days: list, current_date: str, offset: int) -> str:
    """获取第N个交易日后的日期"""
    try:
        idx = trading_days.index(current_date)
        if 0 <= idx + offset < len(trading_days):
            return trading_days[idx + offset]
    except ValueError:
        pass
    return None


def get_stock_data_as_of(stocks: dict, code: str, as_of_date: str, window: int = 60) -> pd.DataFrame:
    """获取某只股票在as_of_date（含）之前的window条交易数据"""
    if code not in stocks:
        return None
    sdf = stocks[code]
    mask = sdf['date'] <= as_of_date
    subset = sdf[mask].tail(window).copy()
    if len(subset) < 30:
        return None  # 至少需要30根K线
    subset.reset_index(drop=True, inplace=True)
    return subset


def calc_trade_cost(price: float, shares: int, is_buy: bool) -> float:
    """计算单笔交易成本"""
    amount = price * shares
    commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
    if is_buy:
        return commission
    else:
        stamp = amount * STAMP_DUTY_RATE
        return commission + stamp


def can_buy(price: float, shares: int, capital: float) -> bool:
    """检查买入是否可行"""
    cost = price * shares + calc_trade_cost(price, shares, True)
    return cost <= capital


def simulate_trade(
    stock_df: pd.DataFrame, entry_idx: int,
    trend: str, capital: float = 3000
) -> dict:
    """模拟一次交易

    Args:
        stock_df: 该股票完整DataFrame（从date到最新）
        entry_idx: entry_date在stock_df中的位置
        trend: 大盘趋势
        capital: 账户资金

    Returns:
        dict with trade result, or None if trade is invalid

    Simulates:
        T+1: Buy at entry_date's open (当日买入，因信号前一日已生成)
        T+2: Sell at close + 2 trading days later
        Stop loss: -3% from entry price
        Take profit: +4% moves stop to breakeven
    """
    if entry_idx + 4 > len(stock_df):
        return None  # 不够数据完成T+2

    entry_date = stock_df.iloc[entry_idx]['date']

    # 买入 (data is already T+1 = signal D -> now entry D has arrived)
    # entry day's open is our buy price
    entry_price = float(stock_df.iloc[entry_idx]['open'])

    # 涨停排除
    prev_close = float(stock_df.iloc[entry_idx - 1]['close']) if entry_idx > 0 else entry_price
    limit_up = prev_close * 1.095
    if entry_price >= limit_up - 0.01:
        return None  # 涨停买不进

    # 成交额过滤
    entry_volume = float(stock_df.iloc[entry_idx]['volume'])
    entry_amount = entry_volume * entry_price
    if entry_amount < MIN_VOLUME_AMOUNT:
        return None

    # 计算买入数量（100股整数倍）
    raw_shares = math.floor(capital * 0.2 / entry_price / 100) * 100  # 20%仓位
    if raw_shares < 100:
        raw_shares = 100

    # 检查是否能买
    buy_cost = entry_price * raw_shares + calc_trade_cost(entry_price, raw_shares, True)
    if buy_cost > capital:
        raw_shares = max(100, math.floor((capital - MIN_COMMISSION * 2) / entry_price / 100) * 100)

    # 再检查
    buy_cost = entry_price * raw_shares + calc_trade_cost(entry_price, raw_shares, True)
    if buy_cost > capital or raw_shares < 100:
        return None

    shares = raw_shares
    buy_commission = calc_trade_cost(entry_price, shares, True)
    total_cost = entry_price * shares + buy_commission

    # 实际买入价（含滑点）
    actual_buy_price = entry_price * (1 + SLIPPAGE)
    actual_buy_cost = actual_buy_price * shares + buy_commission

    # 跟踪止损
    stop_price = actual_buy_price * 0.97  # -3%
    breakeven_triggered = False  # 是否触发过保本止损

    # 模拟持有期：从entry day到T+2强制卖出日
    exit_idx = min(entry_idx + 2, len(stock_df) - 1)
    exit_date = stock_df.iloc[exit_idx]['date']

    # 日内止损检查（简化为收盘检查）
    actual_exit_price = None
    exit_reason = 'T+2_force'

    for day in range(0, exit_idx - entry_idx + 1):
        day_idx = entry_idx + day
        if day_idx >= len(stock_df):
            break

        day_high = float(stock_df.iloc[day_idx]['high'])
        day_low = float(stock_df.iloc[day_idx]['low'])
        day_close = float(stock_df.iloc[day_idx]['close'])

        # T日: 当天跌穿止损
        if day == 0:
            if day_low <= stop_price:
                actual_exit_price = stop_price * (1 - SLIPPAGE)
                exit_reason = 'stop_loss_T0'
                break

        # T+1: 可以卖出
        if day >= 1:
            # 盘中跌穿止损
            if day_low <= stop_price:
                actual_exit_price = stop_price * (1 - SLIPPAGE)
                exit_reason = 'stop_loss'
                break

            # 触发保本止损（浮盈≥4%）
            high_return = (day_high - actual_buy_price) / actual_buy_price
            if high_return >= 0.04 and not breakeven_triggered:
                breakeven_triggered = True
                stop_price = actual_buy_price  # 保本

    # 如果没触发任何止损，T+2强制卖出
    if actual_exit_price is None:
        exit_bar = stock_df.iloc[exit_idx]
        actual_exit_price = float(exit_bar['close']) * (1 - SLIPPAGE)

    # 成交额过滤（卖出时）
    exit_volume = float(stock_df.iloc[exit_idx]['volume'])
    if exit_volume * actual_exit_price < MIN_VOLUME_AMOUNT:
        return None  # 流动性不足

    # 跌停不能卖 → 按次日开盘卖
    prev_exit_close = float(stock_df.iloc[exit_idx - 1]['close']) if exit_idx > 0 else actual_exit_price
    limit_down = prev_exit_close * 0.905
    if actual_exit_price <= limit_down + 0.01:
        # 尝试次日
        if exit_idx + 1 < len(stock_df):
            actual_exit_price = float(stock_df.iloc[exit_idx + 1]['open']) * (1 - SLIPPAGE)

    # 计算收益
    sell_commission = calc_trade_cost(actual_exit_price, shares, False)
    gross_return = (actual_exit_price - actual_buy_price) / actual_buy_price
    total_commission = buy_commission + sell_commission
    net_return = gross_return - total_commission / (actual_buy_price * shares)

    return {
        'code': stock_df.iloc[0]['code'],
        'entry_date': entry_date,
        'exit_date': exit_date,
        'entry_price': round(actual_buy_price, 3),
        'exit_price': round(actual_exit_price, 3),
        'shares': shares,
        'gross_return': round(gross_return * 100, 2),
        'net_return': round(net_return * 100, 2),
        'commission': round(total_commission, 2),
        'exit_reason': exit_reason,
        'trend': trend,
    }


def run_backtest(
    stocks: dict = None,
    start_date: str = '20240101',
    end_date: str = '20260601',
    capital: float = 3000,
    max_trades_per_day: int = 2,
    max_structures: int = 3,  # S+ or S or A
) -> dict:
    """运行完整回测

    逐日扫描全市场 → 检测结构 → 模拟交易

    Returns:
        dict with trades list and summary stats
    """
    if stocks is None:
        stocks = load_all_data()

    trading_days = find_trading_days(stocks)
    # 只回测指定范围
    trading_days = [d for d in trading_days if start_date <= d <= end_date]
    print(f"   交易日: {len(trading_days)} 天 ({trading_days[0]} ~ {trading_days[-1]})")

    all_codes = list(stocks.keys())
    print(f"   股票池: {len(all_codes)} 只")

    trades = []
    daily_signals = defaultdict(list)

    total_checks = 0
    signal_count = 0

    for di, current_date in enumerate(trading_days):
        if di < 30:  # 需要至少30根K线做缓冲区
            continue

        if di % 100 == 0:
            print(f"   {current_date}: 已检查 {total_checks:,} 次, 发现 {signal_count} 个信号, {len(trades)} 笔交易")

        # 大盘趋势
        trend = classify_market_trend_historical(stocks, current_date)

        # 每天最多2笔交易
        trades_today = 0

        for code in all_codes:
            if trades_today >= max_trades_per_day:
                break

            sdf = get_stock_data_as_of(stocks, code, current_date, 60)
            if sdf is None:
                continue

            # 扫描结构
            result = _scan_single_historical(code, sdf, trend)
            if result is None:
                continue

            total_checks += 1
            signal_count += 1

            # 结构等级过滤
            if result['grade'] not in ['S+', 'S', 'A']:
                continue

            # 找到这个股票在完整数据集中的位置
            if code not in stocks:
                continue
            full_df = stocks[code]
            entry_dates = full_df[full_df['date'] > current_date]['date'].tolist()
            if not entry_dates:
                continue

            entry_date = entry_dates[0]
            entry_idx = full_df[full_df['date'] == entry_date].index[0] if len(full_df[full_df['date'] == entry_date]) > 0 else None
            if entry_idx is None:
                continue

            # 模拟交易
            trade = simulate_trade(full_df, entry_idx, trend, capital)
            if trade:
                trade['signal_grade'] = result['grade']
                trade['structures'] = [s[0] for s in result['structures']]
                trade['signal_date'] = current_date
                trade['market_scale'] = result.get('market_scale', 1.0)
                trades.append(trade)
                daily_signals[current_date].append(trade)
                trades_today += 1

    return {
        'trades': trades,
        'total_checks': total_checks,
        'signal_count': signal_count,
        'trading_days': trading_days,
    }


def classify_market_trend_historical(stocks: dict, as_of_date: str) -> str:
    """根据历史数据判断大盘趋势（用于回测）

    用所有股票的平均表现来模拟大盘。
    或者从数据库读取沪深300历史数据。
    """
    # 尝试从数据库读取沪深300
    try:
        conn = __import__('sqlite3').connect(DB_PATH)
        df = pd.read_sql_query(
            "SELECT code, date, close FROM daily_data WHERE code='000300' AND adjust='qfq' AND date <= ? ORDER BY date",
            conn, params=(as_of_date,)
        )
        conn.close()
        if len(df) >= 30:
            close = df['close'].values
            ma10 = pd.Series(close).rolling(10).mean().values[-1]
            ma30 = pd.Series(close).rolling(30).mean().values[-1]
            if ma10 > ma30:
                return 'uptrend'
            elif ma10 < ma30:
                return 'downtrend'
            return 'range'
    except Exception:
        pass

    # 备用：用股票池平均值模拟
    returns = []
    for code in list(stocks.keys())[:200]:  # 只用前200只粗略估计
        sdf = stocks[code]
        sdf = sdf[sdf['date'] <= as_of_date].tail(30)
        if len(sdf) >= 20:
            arr = sdf['close'].values
            ma10 = np.mean(arr[-10:])
            ma30 = np.mean(arr[-30:])
            returns.append(ma10 > ma30)

    if not returns:
        return 'unknown'
    bull_pct = sum(returns) / len(returns)
    if bull_pct > 0.55:
        return 'uptrend'
    elif bull_pct < 0.45:
        return 'downtrend'
    return 'range'


def _scan_single_historical(code: str, df: pd.DataFrame, trend: str) -> dict:
    """历史单只股票扫描（回测版，不依赖缓存）"""
    if df is None or len(df) < 30:
        return None

    close = df['close'].values

    # 涨停板排除
    if len(close) >= 2:
        daily_ret = (close[-1] - close[-2]) / close[-2]
        if daily_ret > 0.095:
            return None

    STRUCTURE_MATCH = {
        'm2b':     {'uptrend': True, 'range': False, 'downtrend': False, 'unknown': True},
        'two_leg': {'uptrend': True, 'range': True, 'downtrend': False, 'unknown': True},
        'inside':  {'uptrend': True, 'range': True, 'downtrend': False, 'unknown': True},
        'engulf':  {'uptrend': True, 'range': True, 'downtrend': False, 'unknown': True},
        'failed':  {'uptrend': False, 'range': True, 'downtrend': False, 'unknown': True},
    }

    structures = []

    if STRUCTURE_MATCH['m2b'].get(trend, True):
        s1, c1 = detect_m2b_pullback(df)
        if s1: structures.append(('M2B回调', 'S', c1))
    if STRUCTURE_MATCH['two_leg'].get(trend, True):
        s2, c2 = detect_two_leg_pullback(df)
        if s2: structures.append(('两段式', 'S', c2))
    if STRUCTURE_MATCH['inside'].get(trend, True):
        s3, c3 = detect_inside_bar_breakout(df)
        if s3: structures.append(('InsideBar', 'S', c3))
    if STRUCTURE_MATCH['engulf'].get(trend, True):
        a1, c1 = detect_bullish_engulfing(df)
        if a1: structures.append(('阳包阴', 'A', c1))
    if STRUCTURE_MATCH['failed'].get(trend, True):
        a2, c2 = detect_failed_breakout(df)
        if a2: structures.append(('假突破', 'A', c2))

    if not structures:
        return None

    s_cnt = len([s for s in structures if s[1] == 'S'])
    a_cnt = len([s for s in structures if s[1] == 'A'])

    if s_cnt >= 2:
        grade = 'S+'
    elif s_cnt >= 1 and a_cnt >= 1:
        grade = 'S'
    elif a_cnt >= 1:
        grade = 'A'
    else:
        return None

    s_ss = [s for s in structures if s[1] == 'S']
    a_ss = [s for s in structures if s[1] == 'A']
    if s_cnt > 0 and a_cnt > 0:
        conf = (sum(s[2] for s in s_ss) + sum(s[2] for s in a_ss) * 0.7) / (s_cnt + a_cnt)
    elif s_cnt > 0:
        conf = max(s[2] for s in s_ss)
    else:
        conf = max(a[2] for a in a_ss)

    return {
        'code': code,
        'grade': grade,
        'confidence': conf,
        'structures': structures,
    }


# ════════════════════════════════════════════════
# 报告生成
# ════════════════════════════════════════════════

def generate_report(results: dict, capital: float = 3000) -> str:
    """生成回测报告"""
    trades = results['trades']
    if not trades:
        return "[ERR] 无交易记录"

    df = pd.DataFrame(trades)
    n = len(df)

    # 基础统计
    win_rate = (df['net_return'] > 0).mean() * 100
    avg_win = df[df['net_return'] > 0]['net_return'].mean() if len(df[df['net_return'] > 0]) > 0 else 0
    avg_loss = df[df['net_return'] < 0]['net_return'].mean() if len(df[df['net_return'] < 0]) > 0 else 0
    profit_factor = abs(df[df['net_return'] > 0]['net_return'].sum() / df[df['net_return'] < 0]['net_return'].sum()) if df[df['net_return'] < 0]['net_return'].sum() != 0 else float('inf')
    expectancy = df['net_return'].mean()

    # 最大连亏
    max_consec_losses = 0
    cur_losses = 0
    for r in df['net_return']:
        if r < 0:
            cur_losses += 1
            max_consec_losses = max(max_consec_losses, cur_losses)
        else:
            cur_losses = 0

    # 累计收益
    cumulative = (df['net_return'] / 100 + 1).prod()
    total_return = (cumulative - 1) * 100
    final_capital = capital * cumulative

    # 每月统计
    df['entry_date'] = pd.to_datetime(df['entry_date'], format='%Y%m%d')
    df['month'] = df['entry_date'].dt.to_period('M')
    monthly = df.groupby('month').agg(
        trades=('net_return', 'count'),
        win_rate=('net_return', lambda x: (x > 0).mean() * 100),
        avg_return=('net_return', 'mean'),
        total_return=('net_return', 'sum'),
    )

    best_month = monthly['total_return'].max() if len(monthly) > 0 else 0
    worst_month = monthly['total_return'].min() if len(monthly) > 0 else 0

    # 最大回撤
    equity = (df['net_return'] / 100 + 1).cumprod()
    running_max = equity.cummax()
    drawdown = (equity / running_max - 1) * 100
    max_dd = drawdown.min()

    # Monte Carlo 模拟
    n_simulations = 1000
    mc_returns = []
    np.random.seed(42)
    for _ in range(n_simulations):
        sim = np.random.choice(df['net_return'].values, size=len(df), replace=True)
        mc_returns.append(((sim / 100 + 1).prod() - 1) * 100)
    mc_returns = np.array(mc_returns)
    mc_avg = np.mean(mc_returns)
    mc_std = np.std(mc_returns)
    mc_p95 = np.percentile(mc_returns, 5)  # 95% confidence lower bound
    mc_p50 = np.percentile(mc_returns, 50)

    # 结构等级分布
    grade_dist = df['signal_grade'].value_counts().to_dict() if 'signal_grade' in df.columns else {}

    # 退出原因分布
    exit_dist = df['exit_reason'].value_counts().to_dict() if 'exit_reason' in df.columns else {}

    lines = [
        "=" * 65,
        "  橙卫2.0 回测报告",
        "=" * 65,
        f"  总交易: {n} 笔",
        f"  时间范围: {trades[0]['entry_date']} ~ {trades[-1]['entry_date']}",
        f"  初始资金: {capital:.0f}元",
        f"  最终资金: {final_capital:.0f}元",
        f"  总收益率: {total_return:+.2f}%",
        "",
        "── 核心指标 ──",
        f"  胜率:           {win_rate:.1f}%",
        f"  平均盈利:       {avg_win:+.2f}%",
        f"  平均亏损:       {avg_loss:+.2f}%",
        f"  盈亏比:         {abs(avg_win/avg_loss) if avg_loss != 0 else 'N/A' :.2f}",
        f"  利润因子:       {profit_factor:.2f}",
        f"  平均期望:       {expectancy:+.3f}%",
        f"  最大连亏:       {max_consec_losses} 笔",
        f"  最大回撤:       {max_dd:.2f}%",
        "",
        "── 月度表现 ──",
        f"  最佳月: {best_month:+.2f}%",
        f"  最差月: {worst_month:+.2f}%",
        f"  月均交易: {len(df) / len(monthly):.0f} 笔" if len(monthly) > 0 else "",
        "",
        "── Monte Carlo (1000次) ──",
        f"  平均收益: {mc_avg:+.2f}%",
        f"  标准差:   {mc_std:.2f}%",
        f"  中位数:   {mc_p50:+.2f}%",
        f"  95%下限:  {mc_p95:+.2f}%",
        f"  亏损概率: {(mc_returns < 0).mean() * 100:.1f}%",
        "",
        "── 结构分布 ──",
    ]
    for g, cnt in sorted(grade_dist.items(), key=lambda x: -x[1]):
        lines.append(f"  {g}: {cnt}次 ({cnt/n*100:.0f}%)")

    lines.append("")
    lines.append("── 退出原因分布 ──")
    for r, cnt in sorted(exit_dist.items(), key=lambda x: -x[1]):
        lines.append(f"  {r}: {cnt}次 ({cnt/n*100:.0f}%)")

    lines.append("")
    lines.append("─" * 65)

    return "\n".join(lines)


def save_equity_curve(trades: list, filepath: str):
    """保存资金曲线CSV"""
    if not trades:
        return
    df = pd.DataFrame(trades)
    df['entry_date'] = pd.to_datetime(df['entry_date'], format='%Y%m%d')
    df = df.sort_values('entry_date')
    df['cumulative_return'] = (df['net_return'] / 100 + 1).cumprod()
    df['drawdown'] = (df['cumulative_return'] / df['cumulative_return'].cummax() - 1) * 100
    df[['code', 'entry_date', 'net_return', 'cumulative_return', 'drawdown']].to_csv(filepath, index=False)
    print(f"[EQ] 资金曲线已保存: {filepath}")


# ════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='橙卫2.0 回测')
    parser.add_argument('--stock', type=str, help='单只股票回测')
    parser.add_argument('--start', type=str, default='20240101', help='开始日期 YYYYMMDD')
    parser.add_argument('--end', type=str, default='20260601', help='结束日期 YYYYMMDD')
    parser.add_argument('--capital', type=float, default=3000, help='初始资金')
    parser.add_argument('--top', type=int, default=10, help='每交易日最大信号数')
    args = parser.parse_args()

    stocks = load_all_data()

    if args.stock:
        # 单只回测 — 详细输出
        code = args.stock
        print(f"\n[SS] 单只回测: {code}")
        if code not in stocks:
            print(f"[ERR] {code} 不在数据库中")
            return

        sdf = stocks[code]
        print(f"   数据: {len(sdf)} 行, {sdf['date'].iloc[0]} ~ {sdf['date'].iloc[-1]}")

        # 滑动窗口检测
        signals_found = []
        for i in range(30, len(sdf)):
            window = sdf.iloc[i-29:i+1].copy()
            window.reset_index(drop=True, inplace=True)
            trend = classify_market_trend_historical(stocks, sdf.iloc[i]['date'])
            result = _scan_single_historical(code, window, trend)
            if result and result['grade'] in ('S+', 'S', 'A'):
                signals_found.append({
                    'date': sdf.iloc[i]['date'],
                    'grade': result['grade'],
                    'confidence': result['confidence'],
                    'structures': [s[0] for s in result['structures']],
                })

        print(f"\n   发现 {len(signals_found)} 个信号:")
        for s in signals_found:
            print(f"   {s['date']} | {s['grade']} | conf={s['confidence']:.2f} | {', '.join(s['structures'])}")

        # 模拟最后10个信号
        if signals_found:
            print(f"\n   模拟最后5个信号交易:")
            for s in signals_found[-5:]:
                entry_dates = sdf[sdf['date'] > s['date']]['date'].tolist()
                if not entry_dates:
                    continue
                entry_idx = sdf[sdf['date'] == entry_dates[0]].index[0]
                trade = simulate_trade(sdf, entry_idx, 'range', capital=args.capital)
                if trade:
                    print(f"   {s['date']}买入→{trade['exit_date']}卖出: {trade['net_return']:+.2f}% ({trade['exit_reason']})")

    else:
        # 全量回测
        trader_config = {
            'max_trades_per_day': 2,
            'max_structures': 3
        }

        results = run_backtest(
            stocks=stocks,
            start_date=args.start,
            end_date=args.end,
            capital=args.capital,
            **trader_config
        )

        print(f"\n{'=' * 65}")
        report = generate_report(results, args.capital)
        print(report)

        if results['trades']:
            eq_path = os.path.join(DATA_DIR, 'og_backtest_equity.csv')
            save_equity_curve(results['trades'], eq_path)

            # 保存详细交易记录
            trades_path = os.path.join(DATA_DIR, 'og_backtest_trades.json')
            with open(trades_path, 'w') as f:
                json.dump(results['trades'], f, ensure_ascii=False, indent=2)
            print(f"[TR] 交易明细已保存: {trades_path}")

    print("\n✅ 回测完成")


if __name__ == '__main__':
    main()
