"""优化版净值曲线（大盘过滤+动态止盈）"""
import sqlite3, numpy as np, pandas as pd, warnings, sys, os
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

mkt = pd.read_csv(os.path.join(os.path.dirname(__file__), 'market_daily.csv'))
mkt_dict = dict(zip(mkt['date'], mkt['market_ret']))
SLIPPAGE = 0.001; INIT = 100000; POS_PCT = 0.30; MAX_DAY = 3

conn = sqlite3.connect(DB_PATH)
all_codes = [r[0] for r in conn.execute("SELECT code FROM daily_data GROUP BY code HAVING COUNT(*) > 40").fetchall()]
conn.close()
np.random.seed(42)
shuf = np.random.permutation(all_codes).tolist()
test = shuf[len(shuf)//2:len(shuf)//2+500]
print(f'Test set: {len(test)} stocks')

# 收集所有信号
def get_trades():
    trades = []
    conn = sqlite3.connect(DB_PATH)
    for code in test:
        df = pd.read_sql_query("SELECT date,open,close,high,low,volume FROM daily_data WHERE code=? ORDER BY date", conn, params=(code,))
        if df.empty or len(df) < 30: continue
        for c in ['open','close','high','low','volume']: df[c] = pd.to_numeric(df[c], errors='coerce')
        s = df[df['date']>='2026-02-01'].index.min()
        e = df[df['date']<='2026-05-28'].index.max()
        if pd.isna(s) or pd.isna(e): continue
        s, e = int(s), int(e)
        for idx in range(s+1, e+1):
            d1 = df.iloc[idx-1]; d2 = df.iloc[idx]
            date = str(df.iloc[idx]['date'])
            mret = mkt_dict.get(date, 0)
            if mret < 0: continue  # 大盘过滤
            d1_chg = (d1['close']/d1['open']-1)*100
            if d1_chg < 4 or d1['close'] <= d1['open']: continue
            lb = min(20, idx-1)
            pv = df.iloc[idx-1-lb:idx-1]['volume']
            vr = d1['volume']/max(pv.mean(), 1) if len(pv)>0 else 1
            if vr < 1.2: continue
            dr = d1['high']-d1['low']
            cp = (d1['close']-d1['low'])/dr if dr>0 else 0.5
            if cp < 0.6: continue
            if (d2['open']/d1['close']-1)*100 > -1.0: continue
            if d2['low'] > d1['open']*1.04: continue
            buy = min(d1['open']*1.01, (d1['open']+d1['close'])/2) * 1.001
            tp = 1.03 if d1_chg < 6 else (1.05 if d1_chg < 8 else 1.07)
            for hold in range(4):
                ex = idx+hold
                if ex >= len(df): break
                ep = float(df.iloc[ex]['close'])*(1-SLIPPAGE)
                pnl = (ep/buy-1)*100
                trades.append({'date':date,'code':code,'buy':round(buy,2),'pnl':round(pnl,2),'hold':hold,'win':pnl>0,'d1_chg':round(d1_chg,1),'tp':round((tp-1)*100,1)})
                if pnl >= (tp-1)*100 or pnl <= -2: break
            break
    conn.close()
    return trades

trades = get_trades()
t1 = [t for t in trades if t['hold']==1]
print(f'T+1: {len(t1)} trades')

# 净值模拟
t1.sort(key=lambda x: x['date'])
dates = sorted(set(t['date'] for t in t1))
capital = INIT; max_eq = INIT; curve = []; day_pos = []
for date in dates:
    day_trades = [t for t in t1 if t['date']==date]
    day_pnl = 0; cnt = 0
    for t in day_trades:
        if cnt >= MAX_DAY: break
        amt = capital * POS_PCT
        pnl_val = amt * t['pnl'] / 100
        day_pnl += pnl_val; cnt += 1
    capital += day_pnl
    max_eq = max(max_eq, capital)
    dd = (capital-max_eq)/max_eq*100
    curve.append({'date':date,'capital':round(capital,2),'pnl':round(day_pnl,2),'trades':cnt,'dd':round(dd,2)})

ec = pd.DataFrame(curve)
total_ret = (capital/INIT-1)*100
months = max(1, len(dates)/21)
mret = (capital/INIT)**(1/months)-1
sharpe = np.mean(ec['pnl'])/max(np.std(ec['pnl']),0.01)*np.sqrt(252)

print(f'\n{"="*55}')
print(f'  Optimized Equity Curve (Market Filter + Dynamic TP)')
print(f'  Test Set Only')
print(f'{"="*55}')
print(f'  Initial: {INIT:,}')
print(f'  Final:   {capital:,.0f}')
print(f'  Return:  +{total_ret:.1f}%')
print(f'  Months:  {months:.1f}')
print(f'  Monthly: {mret*100:+.1f}%')
print(f'  Annual:  {mret*12*100:+.1f}%')
print(f'  Max DD:  {ec["dd"].min():.1f}%')
print(f'  Sharpe:  {sharpe:.2f}')
print(f'  Trades:  {len(t1)}')
print(f'  WR:      {sum(1 for t in t1 if t["win"])/len(t1)*100:.1f}%')
print(f'  Avg:     {np.mean([t["pnl"] for t in t1]):+.2f}%')
print(f'{"="*55}')

# 月度明细
ec['month'] = ec['date'].str[:7]
monthly = ec.groupby('month').agg(days=('date','count'), trades=('trades','sum'), pnl_sum=('pnl','sum'), end_cap=('capital','last')).round(2)
for idx, row in monthly.iterrows():
    ret_pct = (row['end_cap'] / (row['end_cap'] - row['pnl_sum']) - 1) * 100 if (row['end_cap'] - row['pnl_sum']) > 0 else 0
    print(f'  {idx}: {row["days"]}d {int(row["trades"])}trades {ret_pct:+.1f}% cap={row["end_cap"]:,.0f}')

print(f'\nOptimized vs Baseline:')
print(f'  Baseline (no filter):  wr=81.8% avg=+2.69%')
print(f'  Optimized (market+TP): wr={sum(1 for t in t1 if t["win"])/len(t1)*100:.1f}% avg={np.mean([t["pnl"] for t in t1]):+.2f}%')

ec.to_csv(os.path.join(os.path.dirname(__file__), 'equity_curve_optimized.csv'), index=False)
print(f'\nSaved to equity_curve_optimized.csv')
