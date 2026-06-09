#!/usr/bin/env python3
"""
龙虎榜跟庄策略 (LHB Follow Strategy)

数据源：akshare stock_lhb_detail_em
策略逻辑：龙虎榜净买入占比最高的股票 → 次日开盘买入 → T+1卖出

核心发现：
  净买额占总成交比 > 10%  → 次日胜率60.1%, 均值+1.94%
  涨停+净买额占比 > 10%   → 次日胜率61.6%, 均值+2.23%

资金：10000元（加上佣金门槛5元 = 0.05%）
"""
import sys, os, json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR, logger, load_json, save_json

SIGNAL_FILE = os.path.join(DATA_DIR, 'lhb_signals.json')
TRADE_LOG = os.path.join(DATA_DIR, 'lhb_trades.json')

# 策略参数
COMMISSION = 5.0  # 最低佣金5元
STAMP_DUTY = 0.0005  # 印花税0.05%
CAPITAL = 10000.0
MIN_NET_BUY_RATIO = 8.0  # 净买额占比阈值(%)
MAX_HOLD_DAYS = 2  # 最长持有天数
STOP_LOSS = -0.03  # -3%止损


def fetch_lhb_data(days_back: int = 30) -> pd.DataFrame:
    """拉取龙虎榜数据

    Args:
        days_back: 拉取最近N天数据
    Returns:
        DataFrame with 龙虎榜数据
    """
    import akshare as ak
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days_back)).strftime('%Y%m%d')
    try:
        df = ak.stock_lhb_detail_em(start_date=start, end_date=end)
        if df is None or len(df) == 0:
            return None
        # 数据类型转换
        for col in ['龙虎榜净买额', '净买额占总成交比', '上榜后1日', '上榜后2日',
                     '换手率', '涨跌幅', '收盘价']:
            if col in df.columns:
                df[col] = df[col].astype(float)
        return df
    except Exception as e:
        logger.error(f"龙虎榜数据拉取失败: {e}")
        return None


def generate_signals(df: pd.DataFrame, min_ratio: float = MIN_NET_BUY_RATIO) -> list:
    """生成次日买入信号

    Args:
        df: 龙虎榜DataFrame
        min_ratio: 最小净买额占比阈值

    Returns:
        list of signal dicts, 按信号强度排序
    """
    if df is None or len(df) == 0:
        return []

    # 过滤条件
    # 排除ST/退市/B股/科创板/北交所
    valid_names = ~df['名称'].str.contains('ST|退市|B股', na=False)
    valid_codes = ~df['代码'].apply(
        lambda x: str(x).startswith(('8', '688', '900', '200'))
    )

    mask = (
        (df['净买额占总成交比'] > min_ratio) &
        (df['龙虎榜净买额'] > 0) &
        (df['换手率'] < 30) &  # 排除爆炒
        valid_names &
        valid_codes
    )
    candidates = df[mask].copy()

    if len(candidates) == 0:
        return []

    signals = []
    for _, row in candidates.iterrows():
        code = str(row['代码']).zfill(6)
        name = row['名称']

        # 信号强度：净买额占比 + 净买额规模
        ratio = float(row['净买额占总成交比'])
        net_buy = float(row['龙虎榜净买额'])
        strength = ratio * 0.6 + min(net_buy / 1e8, 5) * 0.4

        # 持仓计算
        entry_price = float(row['收盘价'])
        shares = int(CAPITAL * 0.3 / entry_price / 100) * 100
        if shares < 100:
            shares = 100

        buy_amount = shares * entry_price
        if buy_amount + COMMISSION > CAPITAL:
            shares = int((CAPITAL - 20) / entry_price / 100) * 100
            if shares < 100:
                continue

        stop_price = entry_price * (1 + STOP_LOSS)

        signals.append({
            'code': code,
            'name': name,
            'entry_price': round(entry_price, 2),
            'shares': shares,
            'amount': round(shares * entry_price, 2),
            'net_buy_ratio': round(ratio, 2),
            'net_buy_amount': round(net_buy, 0),
            'strength': round(strength, 2),
            'date': str(row['上榜日'])[:10],
            'signal_type': 'lhb_follow',
            'stop_price': round(stop_price, 2),
        })

    # 按信号强度排序
    signals.sort(key=lambda x: x['strength'], reverse=True)
    return signals


def calc_net_return(entry_price: float, exit_price: float, shares: int) -> dict:
    """计算净收益（含佣金和印花税）"""
    buy_amt = entry_price * shares
    sell_amt = exit_price * shares
    buy_comm = max(buy_amt * 0.0002, COMMISSION)
    sell_comm = max(sell_amt * 0.0002, COMMISSION)
    stamp = sell_amt * STAMP_DUTY
    gross_pnl = sell_amt - buy_amt
    net_pnl = gross_pnl - buy_comm - sell_comm - stamp
    return {
        'gross_return': round((sell_amt - buy_amt) / buy_amt * 100, 2),
        'net_return': round(net_pnl / buy_amt * 100, 2),
        'net_pnl': round(net_pnl, 2),
        'commission': round(buy_comm + sell_comm + stamp, 2),
    }


def verify_backtest(days_back: int = 60) -> dict:
    """回测验证：用历史龙虎榜数据模拟交易

    Returns:
        dict with backtest results
    """
    df = fetch_lhb_data(days_back)
    if df is None:
        return {'error': '无法获取数据'}

    # 测试不同阈值
    print(f"{'阈值':>10} | {'信号数':>5} | {'胜率':>6} | {'均值1日':>8} | {'均值2日':>8} | {'最大收益':>8} | {'最大亏损':>8}")
    print("-" * 75)

    results = []
    for threshold in [5, 8, 10, 12, 15, 20]:
        mask = (df['净买额占总成交比'] > threshold) & (df['龙虎榜净买额'] > 0)
        sub = df[mask].copy()
        if len(sub) < 5:
            continue
        d1 = sub['上榜后1日'].astype(float).dropna()
        d2 = sub['上榜后2日'].astype(float).dropna()
        if len(d1) == 0:
            continue
        wr1 = (d1 > 0).mean() * 100
        mean1 = d1.mean()
        wr2 = (d2 > 0).mean() * 100 if len(d2) > 0 else 0
        mean2 = d2.mean() if len(d2) > 0 else 0
        print(f"  占比>{threshold:>4.0f}% | {len(sub):>4} | {wr1:>5.1f}% | {mean1:>+7.2f}% | {mean2:>+7.2f}% | {d1.max():>+7.2f}% | {d1.min():>+7.2f}%")
        results.append({
            'threshold': threshold,
            'n': len(sub),
            'win_rate_d1': wr1,
            'mean_d1': mean1,
            'win_rate_d2': wr2,
            'mean_d2': mean2,
        })

    return {'threshold_results': results}


def save_daily_signals():
    """生成并保存今日信号"""
    df = fetch_lhb_data(5)  # 最近5天
    if df is None:
        logger.error("获取龙虎榜数据失败")
        return []

    signals = generate_signals(df)
    save_json(signals, SIGNAL_FILE)
    logger.info(f"龙虎榜信号已保存: {len(signals)}个")
    return signals


def get_today_signals() -> list:
    """获取今日信号"""
    signals = load_json(SIGNAL_FILE, [])
    return signals


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--backtest', action='store_true', help='回测验证')
    parser.add_argument('--signal', action='store_true', help='生成今日信号')
    parser.add_argument('--days', type=int, default=60, help='回测天数')
    args = parser.parse_args()

    if args.backtest:
        print(f"龙虎榜回测 (最近{args.days}天)")
        print("=" * 60)
        verify_backtest(args.days)

    if args.signal:
        signals = save_daily_signals()
        print(f"\n今日信号 ({len(signals)}个):")
        for s in signals[:5]:
            print(f"  {s['code']} {s['name']} | 占比{s['net_buy_ratio']}% | 强度{s['strength']} | {s['entry_price']}元 -> {s['shares']}股 = {s['amount']}元")
