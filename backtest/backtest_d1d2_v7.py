"""D1D2回测v7 — 跟踪止盈 + D3动量分叉 + 仓位权重"""
import sys,os,json,warnings,time
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd,numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor,as_completed
import sqlite3
from config import DB_PATH

TRAIL_PCT=3.0; TRAIL_WIDE=5.0; MAX_HOLD=5

def get_sl_tp(d1_chg):
    if d1_chg>=10: return 0.93,1.07
    elif d1_chg>=8: return 0.95,1.05
    elif d1_chg>=6: return 0.97,1.04
    else: return 0.98,1.03

def calc_wt(d1_chg,pb):
    if d1_chg>=8 and 3<=pb<5: return 2.5
    if d1_chg>=8: return 1.5
    if d1_chg>=6 and 3<=pb<5: return 1.5
    if pb<2: return 0.5
    return 1.0

def check_pattern(code):
    trades=[]
    try:
        conn=sqlite3.connect(DB_PATH)
        df=pd.read_sql_query('SELECT * FROM daily_data WHERE code=? ORDER BY date',conn,params=(code,)); conn.close()
        if df.empty or len(df)<60: return trades
        for c in ['open','close','high','low','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
        df=df.dropna(subset=['open','close','high','low','volume'])
        idx=2
        while idx<len(df):
            try:
                d1=df.iloc[idx-1]; d2=df.iloc[idx]
                d1_chg=(d1['close']/d1['open']-1)*100
                if d1_chg>=5 and d1['close']>d1['open']:
                    lookback=min(20,idx)
                    avg_vol=df.iloc[idx-1-lookback:idx-1]['volume'].mean()
                    vr=d1['volume']/avg_vol if avg_vol>0 else 2
                    dr=d1['high']-d1['low']
                    cp=(d1['close']-d1['low'])/dr if dr>0 else 0.5
                    dc_=float(d1['close']); do_=float(d1['open'])
                    if vr>=1.3 and cp>=0.6:
                        pb=(dc_-float(d2['close']))/dc_*100
                        if 1.0<=pb<=8 and float(d2['close'])>float(d2['open']):
                            bp=max(float(d2['low'])*1.003,do_*0.97)
                            sp,tp_=get_sl_tp(d1_chg)
                            sl=bp*sp; tp=bp*tp_
                            tr=False; pk=bp
                            ex=None; h=0; r=""
                            d3p=False; tpt=TRAIL_PCT
                            for hold in range(1,MAX_HOLD+1):
                                ei=idx+hold
                                if ei>=len(df): break
                                dy=df.iloc[ei]
                                dh=float(dy['high']); dl=float(dy['low']); dc2=float(dy['close'])
                                if dl<=sl: ex=max(dl,sl); h=hold; r="止损"; break
                                if tr:
                                    pk=max(pk,dh)
                                    sa=pk*(1-tpt/100)
                                    if dl<=sa: ex=max(dl,sa); h=hold; r="跟踪止盈"; break
                                else:
                                    if dh>=tp: tr=True; pk=dh
                                # D3动量分叉
                                if hold==2 and not d3p:
                                    d3p=True
                                    do2=float(dy['open'])
                                    if dc2<=do2: ex=dc2; h=hold; r="D3转弱"; break
                                    else: tpt=TRAIL_WIDE
                                if hold==MAX_HOLD: ex=dc2; h=hold; r="强卖"
                            if ex is not None:
                                pnl=(ex/bp-1)*100
                                trades.append({
                                    'code':code,
                                    'd1_date':str(d1['date'])[:10],
                                    'entry_date':str(d2['date'])[:10],
                                    'buy_price':round(bp,2),
                                    'exit_date':str(dy['date'])[:10],
                                    'exit_price':round(ex,2),
                                    'pnl_pct':round(pnl,2),
                                    'hold_days':h,'is_win':pnl>0,
                                    'd1_chg':round(d1_chg,1),
                                    'd2_pullback':round(pb,1),
                                    'exit_reason':r,
                                    'weight':calc_wt(d1_chg,pb),
                                })
            except: pass
            idx+=1
    except: pass
    return trades

def get_cached_codes():
    conn=sqlite3.connect(DB_PATH)
    rows=conn.execute('SELECT code,COUNT(*) as cnt FROM daily_data GROUP BY code HAVING cnt>=60').fetchall(); conn.close()
    return sorted([r[0] for r in rows if r[0].startswith(('0','3','6'))
                    and not r[0].startswith(('300','301','688','689','8','4'))])

def print_results(at,label=""):
    if not at: print("no trades"); return
    df=pd.DataFrame(at); wins=df[df["is_win"]]; n=len(df); nw=len(wins)
    print(); print("="*60); print(f"{label}  {n}笔"); print("="*60)
    wr=nw/n*100; mp=df['pnl_pct'].mean(); md=df['pnl_pct'].median()
    print(f'胜率: {wr:.1f}% ({nw}/{n})  均: {mp:+.2f}%  中位: {md:+.2f}%')
    mw=wins['pnl_pct'].mean(); ml=df[~df['is_win']]['pnl_pct'].mean()
    print(f'盈利均: {mw:+.2f}%  亏损均: {ml:+.2f}%')
    wa=(df['pnl_pct']*df['weight']).sum()/df['weight'].sum()
    print(f'加权均: {wa:+.2f}%')
    for e in ["跟踪止盈","止损","D3转弱","强卖"]:
        sub=df[df['exit_reason']==e]
        if sub.empty: continue
        sw=sub[sub['is_win']]['is_win'].sum()/len(sub)*100
        sm=sub['pnl_pct'].mean(); sh=sub['hold_days'].mean()
        print(f'[{e}] {len(sub)}笔 {sw:.1f}% {sm:+.2f}% {sh:.1f}天')
    for t in [5,6,8,10]:
        sub=df[df['d1_chg']>=t]
        if sub.empty: continue
        print(f'D1>={t}%: {len(sub)}笔 {sub['is_win'].mean()*100:.1f}% {sub['pnl_pct'].mean():+.2f}%')
    for lo,hi in [(1,2),(2,3),(3,5),(5,8)]:
        sub=df[(df['d2_pullback']>=lo)&(df['d2_pullback']<hi)]
        if sub.empty: continue
        print(f'D2 {lo}-{hi}%: {len(sub)}笔 {sub['is_win'].mean()*100:.1f}% {sub['pnl_pct'].mean():+.2f}%')
    for h in range(1,MAX_HOLD+1):
        sub=df[df['hold_days']==h]
        if sub.empty: continue
        print(f'hold {h}d: {len(sub)}笔 {sub['is_win'].mean()*100:.1f}% {sub['pnl_pct'].mean():+.2f}%')

if __name__=='__main__':
    t0=time.time()
    print(f'D1D2 v7 D3分叉 trail={TRAIL_PCT}/{TRAIL_WIDE}%')
    print(datetime.now().strftime('%Y-%m-%d %H:%M'))
    codes=get_cached_codes(); print(f"cache: {len(codes)}")
    at=[]; done=0
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures={ex.submit(check_pattern,c):c for c in codes}
        for f in as_completed(futures):
            done+=1
            try: r=f.result()
            except: r=None
            if r: at.extend(r)
            if done%500==0: print(f"[{done}/{len(codes)}] {len(at)} {time.time()-t0:.0f}s")
    print(f"done {len(codes)} {len(at)}trades {time.time()-t0:.0f}s")
    print_results(at,f'v7 D3分叉')
    out=os.path.join(os.path.dirname(os.path.abspath(__file__)),"backtest_d1d2_v7_results.json")
    with open(out,"w") as f: json.dump(at,f,ensure_ascii=False,indent=2)
    print(f"saved {out}")