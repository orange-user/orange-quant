"""波段策略v3: 趋势跟踪+多因子评分"""
import sys,os,json,warnings,time
warnings.filterwarnings('ignore')
sys.path.insert(0,'.')
import pandas as pd,numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor,as_completed
import sqlite3
from config import DB_PATH

SLIPPAGE=0.001;COMM_RATE=0.00025;MIN_COMM=5.0;STAMP_RATE=0.001;IC=10000.0;MP=2;TRAIL_PCT=3;FORCE_DAYS=15

def tc(p,s,b): v=p*s;c=max(v*COMM_RATE,MIN_COMM); return v+c if b else v-c-v*STAMP_RATE

def market_ok():
    """CSI300 above 20MA = market bull filter"""
    try:
        import akshare as ak
        bm=ak.index_zh_a_hist(symbol="000300",period="daily")
        if bm is None or len(bm)<20: return True
        bm=bm.sort_values(bm.columns[0])
        close=bm["close"] if "close" in bm.columns else bm.iloc[:,2]
        ma20=close.rolling(20).mean()
        return float(close.iloc[-1])>float(ma20.iloc[-1])
    except:
        return True

def chk(code):
    trades=[]
    try:
        conn=sqlite3.connect(DB_PATH)
        df=pd.read_sql_query("SELECT date,open,close,high,low,volume FROM daily_data WHERE code=? ORDER BY date",conn,params=(code,))
        conn.close()
        if df.empty or len(df)<60: return trades
        for c_ in ["open","close","high","low","volume"]: df[c_]=pd.to_numeric(df[c_],errors="coerce")
        df=df.dropna(subset=["open","close","high","low","volume"])

        # 因子
        df['ret']=(df['close']/df['open']-1)*100
        df['c_ma60']=(df['close']/df['close'].rolling(60).mean()-1)*100
        df['c_ma20']=(df['close']/df['close'].rolling(20).mean()-1)*100
        df['v_ma20']=df['volume'].rolling(20).mean()
        df['vol_ratio']=df['volume']/df['v_ma20'].replace(0,np.nan)
        df['ma20']=df['close'].rolling(20).mean()
        df['ma20_slope']=df['ma20'].pct_change(5)*100
        df['mom20']=df['close'].pct_change(20)*100
        df['close_pos']=(df['close']-df['low'])/(df['high']-df['low']).replace(0,np.nan)
        df['atr']=df['high'].rolling(14).max()-df['low'].rolling(14).min()
        df['atr_pct']=df['atr']/df['close']*100

        cap=IC;pos=[];i=60
        while i<len(df)-1:
            ds=str(df.iloc[i]["date"])[:10]
            dc2=float(df.iloc[i]["close"])
            for p in pos: p["cv"]=p["sh"]*dc2

            # 卖出
            tr=[]
            for p in pos:
                di=df.iloc[i];dh=float(di["high"]);dl=float(di["low"]);dc=float(di["close"])
                bp=p["bp"];sl=p.get("sl",0);tp=p.get("tp",999)
                if dl<=sl:
                    # 跳空>2%按中间价成交，否则按止损价
                    if abs(dl-sl)/sl>0.02: fill=(sl+dl)/2
                    else: fill=sl
                    ex=fill*(1-SLIPPAGE);n=tc(ex,p["sh"],0);pn=(n-p["bc"])/p["bc"]*100;h=i-p["ei"]
                    trades.append({"c":code,"ed":p["ed"],"bp":round(bp,2),"xd":ds,"ep":round(ex,2),"pn":round(pn,2),"h":h,"w":pn>0,"r":"sl"})
                    cap+=n;tr.append(p);continue
                if p.get("ta"):
                    p["pk"]=max(p["pk"],dh);sa=p["pk"]*(1-TRAIL_PCT/100)
                    if dl<=sa:
                        if abs(dl-sa)/sa>0.02: fill=(sa+dl)/2
                        else: fill=sa
                        ex=fill*(1-SLIPPAGE);n=tc(ex,p["sh"],0);pn=(n-p["bc"])/p["bc"]*100;h=i-p["ei"]
                        trades.append({"c":code,"ed":p["ed"],"bp":round(bp,2),"xd":ds,"ep":round(ex,2),"pn":round(pn,2),"h":h,"w":pn>0,"r":"tp"})
                        cap+=n;tr.append(p);continue
                else:
                    if dh>=tp: p["ta"]=True;p["pk"]=dh
                h=i-p["ei"]
                if h>=FORCE_DAYS:
                    ex=dc*(1-SLIPPAGE*2);n=tc(ex,p["sh"],0);pn=(n-p["bc"])/p["bc"]*100
                    trades.append({"c":code,"ed":p["ed"],"bp":round(bp,2),"xd":ds,"ep":round(ex,2),"pn":round(pn,2),"h":h,"w":pn>0,"r":"f"})
                    cap+=n;tr.append(p)
            for p in tr: pos.remove(p)

            # 买入: 趋势向上+回踩20MA+缩量
            if len(pos)<MP and i+1<len(df):
                di=df.iloc[i];d_next=df.iloc[i+1]
                cm60=float(di["c_ma60"]);ms=float(di["ma20_slope"]);vr=float(di["vol_ratio"])
                r=float(di["ret"]);cp=float(di["close_pos"]);m20=float(di["mom20"])
                cm20=float(di["c_ma20"])

                # 回踩条件: 趋势向上(c_ma60>0), 回踩到20MA附近(c_ma20 -3%~1%), 缩量(vol_ratio<1)
                _csi_ok=_get_csi_cache().get(str(di["date"])[:10],True)
                if cm60>0 and -3<cm20<1 and vr<1 and ms>0 and m20>0 and _csi_ok and float(di["atr_pct"])>2:
                    entry_price=float(d_next["open"])*(1+SLIPPAGE)
                    sh_hands=max(1,int(cap*0.5/MP/(entry_price*100)))
                    sh=sh_hands*100;bc=tc(entry_price,sh,1)
                    if bc<=cap*0.9:
                        sl_pct=0.90;tp_pct=1.12  # 止损10%, 止盈12%
                        pos.append({"ei":i+1,"ed":str(d_next["date"])[:10],"sh":sh,"bp":entry_price,"bc":bc,"sl":entry_price*sl_pct,"tp":entry_price*tp_pct,"pk":entry_price,"ta":False,"cv":sh*entry_price})
                        cap-=bc
            i+=1

        for p in pos:
            ex=float(df.iloc[-1]["close"])*(1-SLIPPAGE*2);n=tc(ex,p["sh"],0);pn=(n-p["bc"])/p["bc"]*100;h=len(df)-1-p["ei"]
            trades.append({"c":code,"ed":p["ed"],"bp":round(p["bp"],2),"xd":str(df.iloc[-1]["date"])[:10],"ep":round(ex,2),"pn":round(pn,2),"h":max(0,h),"w":pn>0,"r":"end"})
            cap+=n
    except:
        pass
    return trades

def gc():
    # Pool: combine akshare A-share list + SQLite for maximum coverage
    conn=sqlite3.connect(DB_PATH)
    db_codes=set(r[0] for r in conn.execute("SELECT DISTINCT code FROM daily_data").fetchall())
    conn.close()
    try:
        import akshare as ak
        ak_codes=set(ak.stock_info_a_code_name()['code'].tolist())
    except:
        ak_codes=set()
    all_codes=db_codes|ak_codes
    eligible=[c for c in all_codes if c.startswith(("0","3","6")) and not c.startswith(("300","301","688","689","8","4"))]
    return sorted(eligible)

# Module-level CSI300 cache (computed once)
_CSI_CACHE=None
def _get_csi_cache():
    global _CSI_CACHE
    if _CSI_CACHE is None:
        try:
            import akshare as ak
            bm=ak.index_zh_a_hist(symbol="000300",period="daily")
            if bm is not None and len(bm)>20:
                bm=bm.sort_values(bm.columns[0])
                c=bm["close"] if "close" in bm.columns else bm.iloc[:,2]
                m20=c.rolling(20).mean()
                _CSI_CACHE={str(r[bm.columns[0]])[:10]:float(c.iloc[i])>float(m20.iloc[i]) for i,r in bm.iterrows()}
            else:
                _CSI_CACHE={}
        except:
            _CSI_CACHE={}
    return _CSI_CACHE

t0=time.time()
print("策略v3b: 趋势多因子评分+条件过滤",flush=True)
codes=gc();print(f"池子: {len(codes)}",flush=True)
at=[];d=0
with ThreadPoolExecutor(max_workers=20) as ex:
    fs={ex.submit(chk,c):c for c in codes}
    for f in as_completed(fs):
        d+=1
        try: r=f.result()
        except: r=None
        if r: at.extend(r)
        if d%500==0: print(f"[{d}/{len(codes)}] {len(at)}笔 {time.time()-t0:.0f}s",flush=True)

print(f"\n完成 {len(at)}笔 {time.time()-t0:.0f}s",flush=True)
if at:
    df=pd.DataFrame(at);n=len(df);nw=df["w"].sum();wr=nw/n*100;mp=df["pn"].mean();md=df["pn"].median()
    print(f"胜率: {wr:.1f}% ({int(nw)}/{n})  均: {mp:+.2f}%  中位: {md:+.2f}%")
    total=df["pn"].sum()
    print(f"累计: {total:+.2f}%")
    # Benchmark CSI300
    try:
        import akshare as ak
        bm=ak.index_zh_a_hist(symbol="000300",period="daily")
        if "date" in bm.columns:
            bm_dates=set(df["ed"].dropna().values)
            bm=bm[bm["date"].astype(str).isin(bm_dates)]
            if len(bm)>1:
                bm_ret=(bm["close"].iloc[-1]/bm["close"].iloc[0]-1)*100
                alpha=total-bm_ret
                print(f"alpha: {alpha:+.2f}%")
    except:
        pass
    json.dump(at,open("backtest/strategy_v3b_10k_results.json","w"),ensure_ascii=False,indent=2)
    print(f"保存: strategy_v3b_results.json")
else:
    print("0笔")
