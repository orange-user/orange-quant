"""回测D1放量大阳 + D2低开洗盘拉起模式"""
import sys, os, json, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from collections import defaultdict

from config import DB_PATH

# ========== D1+D2 模式检测 ==========

def check_d1d2_pattern(df, idx):
    """D1放量大阳 + D2低开洗盘拉起

    D1条件:
      - 涨幅 > 5%
      - 阳线 (close > open)
      - 量比 > 1.3（放量）
      - 收盘在K线上部70%以上（强势收盘）

    D2条件:
      - 低开 (open < prev_close * 0.98, 即低开>2%)
      - 日内最低接近D1开盘价 (low <= D1_open * 1.02)
      - 收盘在D1收盘价之上 (close > D1_close) ← 反包
      - 阳线 (close > open)
    """
    if idx < 2 or idx >= len(df) - 3:  # 需要D1前一天数据 + D2后3天看表现
        return False, None, 0

    d1 = df.iloc[idx - 1]
    d2 = df.iloc[idx]

    # ===== D1条件 =====
    d1_chg = (d1['close'] / d1['open'] - 1) * 100
    if d1_chg < 5:  # 涨幅不够
        return False, None, 0
    if d1['close'] <= d1['open']:  # 不是阳线
        return False, None, 0

    # D1量比
    lookback = min(20, idx)
    prev_volumes = df.iloc[idx-1-lookback:idx-1]['volume'].values
    avg_vol = np.mean(prev_volumes) if len(prev_volumes) > 0 else 1
    d1_vr = d1['volume'] / max(avg_vol, 1)
    if d1_vr < 1.3:  # 放量不够
        return False, None, 0

    # D1强势收盘（收盘在K线上部70%）
    d1_body_top = d1['close']
    d1_body_bottom = d1['open']
    d1_upper = d1['high'] - max(d1['close'], d1['open'])
    d1_lower = min(d1['close'], d1['open']) - d1['low']
    d1_range = d1['high'] - d1['low']
    if d1_range > 0:
        d1_close_position = (d1['close'] - d1['low']) / d1_range
    else:
        d1_close_position = 0.5
    if d1_close_position < 0.6:  # 收盘不够高
        return False, None, 0

    # ===== D2条件（匹配当前monitor.py，用Low模拟盘中触发）=====
    d1_close = d1['close']
    d1_open = d1['open']

    # 条件1: 今日最低回踩到D1开盘价+3%以内
    if d2['low'] > d1_open * 1.03:
        return False, None, 0

    # 条件2: 不能跌破D1开盘价太多
    if d2['low'] < d1_open * 0.97:
        return False, None, 0

    # 条件3(盘中版): 收盘价从最低点反弹超过0.5%（盘中触发过就算数）
    if d2['close'] < d2['low'] * 1.005:
        return False, None, 0

    # 条件4: 阳线
    if d2['close'] <= d2['open']:
        return False, None, 0

    # 低开（统计特征）
    d2_open_chg = (d2['open'] / d1_close - 1) * 100

    score = min(80, int(
        20  # 基础分
        + min(d1_chg * 2, 20)  # D1涨幅加分
        + min(abs(d2_open_chg) * 3, 15)  # 低开幅度加分（洗得越狠越好）
        + 10  # 反包加分
    ))

    return True, 'D1大阳+D2低开反包', score


# ========== 回测主逻辑 ==========

def run_backtest(codes=None, start_date='2026-01-01', end_date='2026-06-01'):
    conn = sqlite3.connect(DB_PATH)
    if codes is None:
        all_codes = [r[0] for r in conn.execute(
            "SELECT code FROM daily_data GROUP BY code HAVING COUNT(*) > 40"
        ).fetchall()]
        np.random.seed(42)
        codes = np.random.choice(all_codes, min(300, len(all_codes)), replace=False).tolist()

    trades = []

    for code in codes:
        df = pd.read_sql_query(
            "SELECT date, open, close, high, low, volume FROM daily_data WHERE code=? ORDER BY date",
            conn, params=(code,))
        if df.empty or len(df) < 40:
            continue
        for c in ['open','close','high','low','volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')

        start_idx = df[df['date'] >= start_date].index.min()
        end_idx = df[df['date'] <= end_date].index.max()
        if pd.isna(start_idx) or pd.isna(end_idx):
            continue
        start_idx = int(start_idx)
        end_idx = int(end_idx)

        for idx in range(start_idx + 1, end_idx + 1):  # +1因为需要前一日数据
            triggered, sig, sc = check_d1d2_pattern(df, idx)
            if triggered:
                entry_date = df.iloc[idx]['date']
                entry_price = float(df.iloc[idx]['close'])
                # 实际买点应在D2日内低位（回踩D1开盘价时），不是收盘价
                d1_open_val = float(df.iloc[idx-1]['open'])
                d2_low_val = float(df.iloc[idx]['low'])
                buy_price = max(d1_open_val * 0.995, d2_low_val * 0.98)  # 近似买入价
                d1_date = df.iloc[idx-1]['date']
                d1_close = float(df.iloc[idx-1]['close'])
                d1_open = float(df.iloc[idx-1]['open'])

                # 跟踪T+0到T+3的表现
                for hold in range(0, 4):
                    exit_idx = idx + hold
                    if exit_idx >= len(df):
                        break
                    exit_price = float(df.iloc[exit_idx]['close'])
                    exit_date = df.iloc[exit_idx]['date']
                    pnl = (exit_price / buy_price - 1) * 100

                    trades.append({
                        'code': code,
                        'entry_date': str(entry_date),
                        'entry_price': round(buy_price, 2),
                        'close_price': round(entry_price, 2),
                        'exit_date': str(exit_date),
                        'exit_price': round(exit_price, 2),
                        'pnl_pct': round(pnl, 2),
                        'hold_days': hold,
                        'is_win': pnl > 0,
                        'd1_date': str(d1_date),
                        'd1_close': round(d1_close, 2),
                        'd1_open': round(d1_open, 2),
                        'd1_chg': round((d1_close/d1_open-1)*100, 1),
                        'd2_low_open': round((entry_price/d1_close-1)*100, 1),
                    })
                break  # 同一只股票多次触发只取第一次（避免连续信号）

    conn.close()

    # 按持有天数分析
    print(f'总信号数: {len(trades)}')
    print()

    for hold in range(4):
        subset = [t for t in trades if t['hold_days'] == hold]
        if not subset:
            continue
        df = pd.DataFrame(subset)
        wins = df[df['is_win']]
        print(f'【持有{hold}天】')
        print(f'  交易次数: {len(df)}')
        print(f'  胜率: {len(wins)/len(df)*100:.1f}% ({len(wins)}/{len(df)})')
        print(f'  平均盈亏: {df["pnl_pct"].mean():+.2f}%')
        print(f'  盈利均值: {df[df["pnl_pct"]>0]["pnl_pct"].mean():+.2f}%')
        print(f'  亏损均值: {df[df["pnl_pct"]<0]["pnl_pct"].mean():+.2f}%')
        print(f'  最大盈利: {df["pnl_pct"].max():+.2f}%')
        print(f'  最大亏损: {df["pnl_pct"].min():+.2f}%')
        print()

    # T+1详细分析
    t1 = [t for t in trades if t['hold_days'] == 1]
    if t1:
        df1 = pd.DataFrame(t1)
        print('=== T+1 详细特征 ===')
        # D1涨幅分组
        for chg_thresh in [5, 7, 10]:
            sub = df1[df1['d1_chg'] >= chg_thresh]
            if not sub.empty:
                wr = sub['is_win'].mean() * 100
                print(f'  D1涨幅>={chg_thresh}%: {len(sub)}笔, 胜率{wr:.1f}%')

        # D2低开幅度分组
        for low_thresh in [-1, -2, -3, -4]:
            sub = df1[df1['d2_low_open'] <= low_thresh]
            if not sub.empty:
                wr = sub['is_win'].mean() * 100
                print(f'  D2低开<={low_thresh}%: {len(sub)}笔, 胜率{wr:.1f}%')

    # 保存结果
    out_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(out_dir, 'backtest_d1d2_results.json'), 'w') as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)
    print(f'\n结果已保存')


if __name__ == '__main__':
    print('加载数据...')
    conn = sqlite3.connect(DB_PATH)
    all_codes = [r[0] for r in conn.execute(
        "SELECT code FROM daily_data GROUP BY code HAVING COUNT(*) > 40"
    ).fetchall()]
    conn.close()
    print(f'可用股票: {len(all_codes)}只')

    print(f'全市场回测: {len(all_codes)}只')

    run_backtest(codes=all_codes, start_date='2026-01-01', end_date='2026-06-01')
