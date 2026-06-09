"""全量回测 D1放量大阳 + D2回踩（基于SQLite缓存，0 API调用）"""
import sys, os, json, warnings, time
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3

from config import DB_PATH


def check_pattern(code):
    """从SQLite缓存读取数据检测D1D2模式，0 API调用"""
    trades = []
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(
            "SELECT * FROM daily_data WHERE code=? ORDER BY date",
            conn, params=(code,)
        )
        conn.close()
        if df.empty or len(df) < 60:
            return trades

        for c in ['open', 'close', 'high', 'low', 'volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['open', 'close', 'high', 'low', 'volume'])

        idx = 2
        while idx < len(df):
            try:
                d1 = df.iloc[idx - 1]
                d2 = df.iloc[idx]
                d1_chg = (d1['close'] / d1['open'] - 1) * 100

                if d1_chg >= 5 and d1['close'] > d1['open']:
                    lookback = min(20, idx)
                    avg_vol = df.iloc[idx - 1 - lookback:idx - 1]['volume'].mean()
                    vr = d1['volume'] / avg_vol if avg_vol > 0 else 2
                    d1_range = d1['high'] - d1['low']
                    close_pos = (d1['close'] - d1['low']) / d1_range if d1_range > 0 else 0.5
                    d1_close = d1['close']

                    if vr >= 1.3 and close_pos >= 0.6:
                        pullback = (d1_close - d2['close']) / d1_close * 100
                        if 1.0 <= pullback <= 8 and d2['close'] > d2['open'] and d2['close'] >= d2['low'] * 1.003:
                            buy_price = max(d2['low'] * 1.003, d1['open'] * 0.97)
                            for hold in range(1, 4):
                                exit_idx = idx + hold
                                if exit_idx >= len(df):
                                    break
                                exit_p = float(df.iloc[exit_idx]['close'])
                                pnl = (exit_p / buy_price - 1) * 100
                                trades.append({
                                    'code': code, 'd1_date': str(d1['date'])[:10],
                                    'entry_date': str(d2['date'])[:10],
                                    'buy_price': round(buy_price, 2),
                                    'exit_date': str(df.iloc[exit_idx]['date'])[:10],
                                    'exit_price': round(exit_p, 2),
                                    'pnl_pct': round(pnl, 2),
                                    'hold_days': hold, 'is_win': bool(pnl > 0),
                                    'd1_chg': round(d1_chg, 1),
                                    'd2_pullback': round(pullback, 1),
                                })
            except:
                pass
            idx += 1
    except:
        pass
    return trades


def get_cached_codes():
    """从SQLite获取有足够缓存的股票列表（0 API调用）"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT code, COUNT(*) as cnt FROM daily_data GROUP BY code HAVING cnt >= 60"
    ).fetchall()
    conn.close()
    codes = sorted([r[0] for r in rows
                    if r[0].startswith(('0', '3', '6'))
                    and not r[0].startswith(('300', '301', '688', '689', '8', '4'))])
    return codes


if __name__ == '__main__':
    t0 = time.time()
    print(f'全市场D1D2回测（SQLite缓存版） {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'条件: D1涨>5%量比>1.3 + D2从D1收盘回撤1~8%')

    codes = get_cached_codes()
    print(f'缓存股票池: {len(codes)}只（0 API调用）')

    all_trades = []
    done = 0
    MAX_WORKERS = 20

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(check_pattern, c): c for c in codes}
        for f in as_completed(futures):
            code = futures[f]
            done += 1
            try:
                trades = f.result()
                if trades:
                    all_trades.extend(trades)
            except:
                pass
            if done % 500 == 0 or done == len(codes):
                elapsed = time.time() - t0
                print(f'  [{done}/{len(codes)}] {len(all_trades)}信号 {elapsed:.0f}s')

    elapsed = time.time() - t0
    print(f'\n{"=" * 50}')
    t1c = sum(1 for t in all_trades if t['hold_days'] == 1)
    print(f'完成! {len(codes)}只, {len(all_trades)}笔交易(T+1={t1c}), 耗时{elapsed:.0f}s')
    print(f'{"=" * 50}\n')

    if not all_trades:
        print('无信号')
        exit()

    for hold in range(1, 4):
        sub = [t for t in all_trades if t['hold_days'] == hold]
        if not sub:
            continue
        df = pd.DataFrame(sub)
        wins = df[df['is_win']]
        print(f'【持有{hold}天】 {len(df)}笔')
        print(f'  胜率: {len(wins) / len(df) * 100:.1f}% ({len(wins)}/{len(df)})')
        print(f'  平均: {df["pnl_pct"].mean():+.2f}% | 最大盈: {df["pnl_pct"].max():+.2f}% | 最大亏: {df["pnl_pct"].min():+.2f}%')

    t1 = [t for t in all_trades if t['hold_days'] == 1]
    if t1:
        df1 = pd.DataFrame(t1)
        print(f'\n=== T+1 特征 ({len(t1)}笔) ===')
        for thresh in [5, 7, 10]:
            sub = df1[df1['d1_chg'] >= thresh]
            if not sub.empty:
                print(f'  D1>={thresh}%: {len(sub)}笔 胜率{sub["is_win"].mean() * 100:.1f}% 均{sub["pnl_pct"].mean():+.2f}%')
        for pb in [2, 3, 5]:
            sub = df1[df1['d2_pullback'] >= pb]
            if not sub.empty:
                print(f'  回撤>={pb}%: {len(sub)}笔 胜率{sub["is_win"].mean() * 100:.1f}% 均{sub["pnl_pct"].mean():+.2f}%')

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_d1d2_v3_results.json')
    with open(out, 'w') as f:
        json.dump(all_trades, f, ensure_ascii=False, indent=2)
    print(f'\n保存: {out}')
