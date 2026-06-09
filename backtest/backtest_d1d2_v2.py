import sys, os, json, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3, numpy as np, pandas as pd
from config import DB_PATH

SLIPPAGE = 0.001

def check_d1d2(df, idx, d1_min_chg=5, d1_min_vr=1.3, d2_max_open=-1.5):
    if idx < 2 or idx >= len(df):
        return False, 0, '', None
    d1 = df.iloc[idx - 1]; d2 = df.iloc[idx]
    d1_chg = (d1['close'] / d1['open'] - 1) * 100
    if d1_chg < d1_min_chg or d1['close'] <= d1['open']:
        return False, 0, '', None
    lb = min(20, idx - 1)
    pv = df.iloc[idx - 1 - lb:idx - 1]['volume'].values
    av = np.mean(pv) if len(pv) > 0 else 1
    vr = d1['volume'] / max(av, 1)
    if vr < d1_min_vr:
        return False, 0, '', None
    dr = d1['high'] - d1['low']
    cp = (d1['close'] - d1['low']) / dr if dr > 0 else 0.5
    if cp < 0.6:
        return False, 0, '', None
    d1_open = d1['open']; d1_close = d1['close']
    d2_open_chg = (d2['open'] / d1_close - 1) * 100
    if d2_open_chg > d2_max_open:
        return False, 0, '', None
    if d2['low'] > d1_open * 1.04:
        return False, 0, '', None
    buy_raw = min(d1_open * 1.01, (d1_open + d1_close) / 2)
    entry_price = buy_raw * (1 + SLIPPAGE)
    return True, round(entry_price, 2), 'D1D2', {
        'd1_open': round(d1_open, 2), 'd1_close': round(d1_close, 2),
        'd1_chg': round(d1_chg, 1), 'd1_vr': round(vr, 2),
        'd2_open_chg': round(d2_open_chg, 1),
    }

def run_backtest(codes, start_date='2026-01-01', end_date='2026-06-01',
                 d1_min_chg=5, d1_min_vr=1.3, d2_max_open=-1.5,
                 max_hold=3):
    conn = sqlite3.connect(DB_PATH)
    trades = []
    for code in codes:
        df = pd.read_sql_query(
            "SELECT date, open, close, high, low, volume FROM daily_data WHERE code=? ORDER BY date",
            conn, params=(code,))
        if df.empty or len(df) < 30:
            continue
        for c in ['open','close','high','low','volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        s = df[df['date'] >= start_date].index.min()
        e = df[df['date'] <= end_date].index.max()
        if pd.isna(s) or pd.isna(e):
            continue
        s, e = int(s), int(e)
        for idx in range(s + 1, e + 1):
            triggered, buy_price, sig, info = check_d1d2(df, idx, d1_min_chg, d1_min_vr, d2_max_open)
            if not triggered:
                continue
            for hold in range(max_hold + 1):
                exit_idx = idx + hold
                if exit_idx >= len(df):
                    break
                exit_price = float(df.iloc[exit_idx]['close']) * (1 - SLIPPAGE)
                pnl = (exit_price / buy_price - 1) * 100
                trades.append({
                    'code': code, 'entry_date': str(df.iloc[idx]['date']),
                    'entry_price': round(buy_price, 2),
                    'exit_price': round(exit_price, 2), 'pnl_pct': round(pnl, 2),
                    'hold_days': hold, 'is_win': pnl > 0,
                })
            break
    conn.close()
    return trades

if __name__ == '__main__':
    conn = sqlite3.connect(DB_PATH)
    all_codes = [r[0] for r in conn.execute(
        "SELECT code FROM daily_data GROUP BY code HAVING COUNT(*) > 40").fetchall()]
    conn.close()
    print(f'Pool: {len(all_codes)}')

    np.random.seed(42)
    train = np.random.choice(all_codes, min(500, len(all_codes)), replace=False).tolist()
    remain = list(set(all_codes) - set(train))
    test = np.random.choice(remain, min(500, len(remain)), replace=False).tolist()
    print(f'Train: {len(train)} Test: {len(test)}')

    print('\nParameter sweep (train set):')
    results = []
    for chg in [4, 5, 6]:
        for vr in [1.2, 1.5]:
            for d2o in [-1.0, -1.5, -2.0]:
                t = run_backtest(train, d1_min_chg=chg, d1_min_vr=vr, d2_max_open=d2o)
                n = len([x for x in t if x['hold_days'] == 0])
                if n >= 10:
                    w = sum(1 for x in t if x['hold_days'] == 0 and x['is_win'])
                    a = np.mean([x['pnl_pct'] for x in t if x['hold_days'] == 0])
                    results.append({'chg': chg, 'vr': vr, 'd2o': d2o, 'n': n, 'wr': w/n*100, 'avg': a})
                    print(f'  D1>{chg}% VR>{vr} D2open<{d2o}%: n={n} wr={w/n*100:.1f}% avg={a:+.2f}%')

    if not results:
        print('No signals found')
        exit()

    best = max(results, key=lambda r: r['n'] if r['wr'] > 50 else 0)
    print(f'\nBest: D1>{best["chg"]}% VR>{best["vr"]} D2open<{best["d2o"]}% '
          f'(train: n={best["n"]} wr={best["wr"]:.1f}% avg={best["avg"]:+.2f}%)')

    print('\nTest set:')
    tt = run_backtest(test, d1_min_chg=best['chg'], d1_min_vr=best['vr'], d2_max_open=best['d2o'])
    n_test = len([x for x in tt if x['hold_days'] == 0])
    if n_test > 0:
        w_test = sum(1 for x in tt if x['hold_days'] == 0 and x['is_win'])
        a_test = np.mean([x['pnl_pct'] for x in tt if x['hold_days'] == 0])
        max_p = max([x['pnl_pct'] for x in tt if x['hold_days'] == 0])
        min_p = min([x['pnl_pct'] for x in tt if x['hold_days'] == 0])
        print(f'  T+0: n={n_test} wr={w_test/n_test*100:.1f}% avg={a_test:+.2f}% max={max_p:+.2f}% min={min_p:+.2f}%')
        t1 = [x for x in tt if x['hold_days'] == 1]
        if t1:
            w1 = sum(1 for x in t1 if x['is_win'])
            a1 = np.mean([x['pnl_pct'] for x in t1])
            print(f'  T+1: n={len(t1)} wr={w1/len(t1)*100:.1f}% avg={a1:+.2f}%')
        print(f'  Overfit check: train wr={best["wr"]:.1f}% vs test wr={w_test/n_test*100:.1f}% (diff={best["wr"]-w_test/n_test*100:.1f}%)')
    else:
        print('  No signals on test set')
