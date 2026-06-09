"""回测monitor的4个买点条件：W底/首阴/N字/放量突破

方法：
  对候选池中每只股票，逐日滑动窗口检测形态触发
  触发后模拟买入，跟踪T+1~T+3表现
  按形态差异化止盈止损，2日强制出局
  输出每个形态的胜率/盈亏/最佳参数

用法：
  python -m backtest.backtest_monitor_patterns
"""
import sys, os, json, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from collections import defaultdict

from config import DB_PATH


# ========== 4个买点条件 ==========

def check_w_bottom(df, as_of_idx):
    """条件A: W底回踩确认 (同monitor.py逻辑)"""
    if as_of_idx < 30:
        return False, None, 0
    recent = df.iloc[max(0,as_of_idx-30):as_of_idx+1]
    if len(recent) < 20:
        return False, None, 0
    closes = recent['close'].values
    highs = recent['high'].values
    lows = recent['low'].values
    # 找波谷
    bottoms = []
    for i in range(2, len(recent)-2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            bottoms.append((i, lows[i]))
    if len(bottoms) < 2:
        return False, None, 0
    for i in range(len(bottoms)):
        for j in range(i+1, len(bottoms)):
            li, lv1 = bottoms[i]
            lj, lv2 = bottoms[j]
            gap = lj - li
            if gap < 5 or gap > 20:
                continue
            ld = abs(lv2-lv1)/max(lv1, lv2)
            if ld > 0.15:
                continue
            mid_h = max(highs[li:lj+1])
            avg_l = (lv1+lv2)/2
            bounce = (mid_h-avg_l)/avg_l
            if bounce < 0.05:
                continue
            neck = mid_h
            cur = closes[-1]
            low_today = lows[-1]
            if low_today <= neck * 1.02 and cur > low_today:
                return True, 'W底回踩确认', min(80, int(20+bounce*200+(1-ld)*30))
    return False, None, 0


def check_dragon_first_yin(df, as_of_idx):
    """条件B: 龙头首阴反包"""
    if as_of_idx < 5:
        return False, None, 0
    recent = df.iloc[max(0,as_of_idx-10):as_of_idx+1]
    if len(recent) < 5:
        return False, None, 0
    closes = recent['close'].values
    opens = recent['open'].values
    highs = recent['high'].values

    # 最近5天是否有3连板
    limit_up_days = 0
    for i in range(-5, -1):
        if abs(i) > len(closes):
            continue
        chg = (closes[i+1] - closes[i]) / closes[i] * 100
        if chg > 9.5:
            limit_up_days += 1
        else:
            limit_up_days = 0
        if limit_up_days >= 3:
            break
    if limit_up_days >= 3:
        yesterday_close = closes[-2]
        yesterday_high = highs[-2]
        day_before_high = highs[-3]
        yesterday_open = opens[-2]
        is_yin = yesterday_close < yesterday_open
        has_upper = yesterday_high > max(yesterday_close, yesterday_open)
        higher_than_prev = yesterday_high > day_before_high
        if is_yin and has_upper and higher_than_prev:
            prev_close = closes[-2]
            if closes[-1] > prev_close:
                return True, '龙头首阴反包', 50
    return False, None, 0


def check_n_pullback(df, as_of_idx):
    """条件C: N字回调到位（MA30替代MA60，适应40条数据）"""
    if as_of_idx < 30:
        return False, None, 0
    recent = df.iloc[max(0,as_of_idx-40):as_of_idx+1]
    if len(recent) < 30:
        return False, None, 0
    closes = recent['close']
    ma20 = closes.rolling(20).mean().iloc[-1]
    ma30_val = closes.rolling(30).mean().iloc[-1] if len(closes) >= 30 else ma20
    cur = closes.iloc[-1]
    if ma20 > ma30_val * 1.03:
        dist = abs(cur - ma20) / ma20 * 100
        if dist < 3 and cur > ma20:
            return True, 'N字回调到位', 40
    return False, None, 0


def check_breakout(df, as_of_idx):
    """条件D: 放量突破"""
    if as_of_idx < 10:
        return False, None, 0
    recent = df.iloc[max(0,as_of_idx-20):as_of_idx+1]
    if len(recent) < 10:
        return False, None, 0
    highs = recent['high'].values
    lows = recent['low'].values
    volumes = recent['volume'].values
    cur = recent['close'].iloc[-1]

    recent_high = max(highs[-5:])
    recent_low = min(lows[-5:])
    amplitude = (recent_high - recent_low) / recent_low * 100

    if amplitude < 15 and cur > recent_high:
        if len(volumes) >= 10:
            avg_vol = np.mean(volumes[-10:-1])
            cur_vol = volumes[-1]
            vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1
            if vol_ratio > 1.5:
                return True, '放量突破', 50
    return False, None, 0


# ========== 回测主逻辑 ==========

def run_backtest(codes=None, start_date='2026-01-01', end_date='2026-06-01'):
    """对指定股票列表逐日回测4个买点条件"""
    conn = sqlite3.connect(DB_PATH)
    if codes is None:
        codes = [r[0] for r in conn.execute(
            "SELECT code FROM daily_data GROUP BY code HAVING COUNT(*) > 60 ORDER BY RANDOM() LIMIT 100"
        ).fetchall()]

    results = defaultdict(list)  # pattern_name -> [{entry_date, entry_price, exit_date, exit_price, pnl, ...}]

    for code in codes:
        df = pd.read_sql_query(
            "SELECT date, open, close, high, low, volume FROM daily_data WHERE code=? ORDER BY date",
            conn, params=(code,))
        if df.empty or len(df) < 40:
            continue

        # 转数值
        for c in ['open','close','high','low','volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')

        start_idx = df[df['date'] >= start_date].index.min()
        end_idx = df[df['date'] <= end_date].index.max()
        if pd.isna(start_idx) or pd.isna(end_idx):
            continue
        start_idx = int(start_idx)
        end_idx = int(end_idx)

        # 逐日检测
        for idx in range(start_idx, end_idx + 1):
            checks = [
                ('W底回踩确认', check_w_bottom),
                ('龙头首阴反包', check_dragon_first_yin),
                ('N字回调到位', check_n_pullback),
                ('放量突破', check_breakout),
            ]
            for pname, check_fn in checks:
                triggered, sig, score = check_fn(df, idx)
                if triggered:
                    entry_date = df.iloc[idx]['date']
                    entry_price = float(df.iloc[idx]['close'])
                    _simulate_trade(code, pname, entry_date, entry_price, df, idx, results)

    conn.close()
    return results


def _simulate_trade(code, pname, entry_date, entry_price, df, entry_idx, results):
    """模拟买入后T+1~T+3, 按形态差异化止盈止损/2日强制出局

    止损/止盈策略（同monitor.py）:
      W底: sl=-3%, tp=+5%
      首阴: sl=-5%, tp=+8%
      N字: sl=-2%, tp=+4%
      放量突破: sl=-3%, tp=+6%
      2日强制卖出
    """
    # 获取止盈止损参数
    params = {
        'W底回踩确认': (0.97, 1.05),
        '龙头首阴反包': (0.95, 1.08),
        'N字回调到位': (0.98, 1.04),
        '放量突破': (0.97, 1.06),
    }
    sl_pct, tp_pct = params.get(pname, (0.97, 1.05))

    stop_loss = entry_price * sl_pct
    target = entry_price * tp_pct

    # 持有期：最多3个交易日
    max_hold = 3
    for hold_day in range(1, max_hold + 1):
        exit_idx = entry_idx + hold_day
        if exit_idx >= len(df):
            break

        exit_price = float(df.iloc[exit_idx]['close'])
        exit_date = df.iloc[exit_idx]['date']
        pnl_pct = (exit_price / entry_price - 1) * 100

        reason = None
        if exit_price >= target:
            reason = '止盈'
        elif exit_price <= stop_loss:
            reason = '止损'
        elif hold_day >= 2:  # 2日强制
            reason = '强制卖出'

        if reason or hold_day == max_hold - 1:
            if not reason:
                reason = '到期卖出'

            results[pname].append({
                'code': code,
                'entry_date': str(entry_date),
                'entry_price': round(entry_price, 2),
                'exit_date': str(exit_date),
                'exit_price': round(exit_price, 2),
                'pnl_pct': round(pnl_pct, 2),
                'hold_days': hold_day,
                'exit_reason': reason,
                'is_win': pnl_pct > 0,
            })
            break


def analyze_results(results):
    """分析回测结果，输出每个条件的胜率/盈亏/关键指标"""
    print(f'\n{"="*60}')
    print(f'  Monitor 4个买点条件回测报告')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'{"="*60}\n')

    total_trades = 0
    for pname, trades in sorted(results.items()):
        if not trades:
            continue
        df = pd.DataFrame(trades)
        wins = df[df['is_win']]
        total = len(df)
        total_trades += total

        print(f'【{pname}】')
        print(f'  交易次数: {total}')
        print(f'  胜  率:   {len(wins)/total*100:.1f}% ({len(wins)}/{total})')
        print(f'  平均盈亏:  {df["pnl_pct"].mean():+.2f}%')
        print(f'  盈亏比:    {df[df["pnl_pct"]>0]["pnl_pct"].mean():+.2f}% / {df[df["pnl_pct"]<0]["pnl_pct"].mean():+.2f}%')
        print(f'  最大盈利:  {df["pnl_pct"].max():+.2f}%')
        print(f'  最大亏损:  {df["pnl_pct"].min():+.2f}%')
        print(f'  平均持有:  {df["hold_days"].mean():.1f}天')
        # 退出原因分布
        reasons = df['exit_reason'].value_counts()
        for r, cnt in reasons.items():
            rwins = df[df['exit_reason']==r]
            rwin_rate = rwins['is_win'].mean() * 100
            print(f'    {r}: {cnt}次 (胜率{rwin_rate:.0f}%)')
        print()

    print(f'{"="*60}')
    print(f'  总计: {total_trades}笔交易')
    print(f'{"="*60}')


if __name__ == '__main__':
    print('加载股票数据...')
    # 取60日线以上的股票，随机200只，平衡大盘小盘
    conn = sqlite3.connect(DB_PATH)
    all_codes = [r[0] for r in conn.execute(
        "SELECT code FROM daily_data GROUP BY code HAVING COUNT(*) > 60"
    ).fetchall()]
    conn.close()
    print(f'符合条件股票: {len(all_codes)}只')

    # 取200只（分市值层次）
    np.random.seed(42)
    sample = np.random.choice(all_codes, min(200, len(all_codes)), replace=False).tolist()

    print(f'回测股票: {len(sample)}只')
    print(f'回测区间: 2026-01-01 ~ 2026-06-01')
    print('开始回测...\n')

    results = run_backtest(codes=sample, start_date='2026-01-01', end_date='2026-06-01')

    # Save raw results
    out_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(out_dir, 'backtest_monitor_results.json'), 'w') as f:
        json.dump({k: v for k, v in results.items()}, f, ensure_ascii=False, indent=2)
    print(f'原始结果已保存')

    analyze_results(results)
