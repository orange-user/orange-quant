import datetime
import json
import os
import numpy as np
import pandas as pd
import akshare as ak
from scipy import stats

from config import *
from data import *


# ==================== 技术指标 ====================
def compute_rsi(closes, period=14):
    delta = closes.diff(); gain = delta.where(delta>0,0.0); loss = -delta.where(delta<0,0.0)
    avg_gain = gain.rolling(period).mean(); avg_loss = loss.rolling(period).mean()
    rs = avg_gain/avg_loss
    rsi = 100 - (100/(1+rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50

def compute_adx(high,low,close,period=14):
    if len(close)<period+1: return None,None,None
    tr = pd.DataFrame({'h-l':high-low,'h-pc':abs(high-close.shift(1)),'l-pc':abs(low-close.shift(1))}).max(axis=1)
    atr = tr.rolling(period).mean()
    up = high.diff(); down = -low.diff()
    plus_dm = up.where((up>0)&(up>down),0)
    minus_dm = down.where((down>0)&(down>up),0)
    plus_di = 100*(plus_dm.rolling(period).mean()/atr)
    minus_di = 100*(minus_dm.rolling(period).mean()/atr)
    dx = 100*abs(plus_di-minus_di)/(plus_di+minus_di)
    adx = dx.rolling(period).mean()
    return float(adx.iloc[-1]), float(plus_di.iloc[-1]), float(minus_di.iloc[-1])

def compute_macd(close,fast=12,slow=26,signal=9):
    ef = close.ewm(span=fast).mean(); es = close.ewm(span=slow).mean()
    macd_line = ef-es; signal_line = macd_line.ewm(span=signal).mean(); hist = macd_line-signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(hist.iloc[-1])

def compute_kdj(high,low,close,n=9):
    lowest = low.rolling(n).min(); highest = high.rolling(n).max()
    rsv = (close-lowest)/(highest-lowest)*100
    k = rsv.ewm(com=2).mean(); d = k.ewm(com=2).mean(); j = 3*k-2*d
    return float(k.iloc[-1]), float(d.iloc[-1]), float(j.iloc[-1])


# ==================== 十大策略引擎 ====================
def strategy_ma_cross(code, closes):
    if len(closes)<20: return 0
    ma5 = closes.rolling(5).mean(); ma20 = closes.rolling(20).mean()
    prev = ma5.iloc[-2] <= ma20.iloc[-2]; curr = ma5.iloc[-1] > ma20.iloc[-1]
    return 20 if prev and curr else (10 if ma5.iloc[-1]>ma20.iloc[-1] else 0)

def strategy_chan_theory(code, high, low, close):
    if len(close)<5: return 0
    for i in range(2,len(close)-2):
        if low.iloc[i] < low.iloc[i-1] and low.iloc[i] < low.iloc[i+1] and low.iloc[i] < low.iloc[i-2] and low.iloc[i] < low.iloc[i+2]:
            neck = max(high.iloc[i-1], high.iloc[i], high.iloc[i+1])
            if close.iloc[-1] > neck: return 15
    return 0

def strategy_wave_theory(code, high, low, close):
    if len(close)<20: return 0
    recent_high = high.iloc[-20:].max(); recent_low = low.iloc[-20:].min()
    rng = recent_high - recent_low
    if rng==0: return 0
    fib_618 = recent_high - rng*0.618
    cur = close.iloc[-1]
    if abs(cur-fib_618)/cur < 0.02: return 20
    elif abs(cur-fib_618)/cur < 0.05: return 10
    return 0

def strategy_bull_trend(code, closes, adx_val, plus_di, minus_di):
    if adx_val is None: return 0
    score = 0
    if adx_val>25 and plus_di>minus_di: score += 10
    if adx_val>30: score += 5
    ma5 = closes.rolling(5).mean(); ma20 = closes.rolling(20).mean()
    if ma5.iloc[-1] > ma20.iloc[-1]: score += 5
    macd,_,hist = compute_macd(closes)
    if hist>0: score += 5
    return min(25,score)

def strategy_hot_topic(code, sectors):
    if not sectors: return 0
    kw = ['AI','人工智能','半导体','芯片','新能源','光伏','储能','军工','低空经济','数据要素','算力']
    score = 0
    for s in sectors:
        for k in kw:
            if k in s: score += 10; break
    return min(25,score)

def strategy_event_driven(code):
    try:
        news = ak.stock_info_global_em().head(20).to_dict('records')
    except:
        return 0
    score = 0
    for n in news:
        title = str(n.get('title','')) + str(n.get('content',''))
        if any(w in title for w in ['涨停','业绩','中标','签署']): score += 8
        if any(w in title for w in ['合作','突破','获批']): score += 5
    return min(25,score)

def strategy_growth_quality(code, info):
    if not info: return 0
    score = 0
    try:
        pe = float(str(info.get('市盈率-动态','0')).replace('亿','').replace('万',''))
        if 0<pe<30: score += 8
        elif 0<pe<50: score += 4
    except: pass
    try:
        pb = float(str(info.get('市净率','0')).replace('亿','').replace('万',''))
        if 0<pb<3: score += 7
    except: pass
    return min(20,score)

def strategy_revaluation(code, info):
    if not info: return 0
    score = 0
    if '增长' in str(info.get('净利润','')): score += 10
    if '增长' in str(info.get('营业收入','')): score += 5
    return min(20,score)

def strategy_dragon_rising(code, closes, highs, lows, opens):
    if len(closes) < 25: return 0
    ma5 = closes.rolling(5).mean()
    ma10 = closes.rolling(10).mean()
    ma20 = closes.rolling(20).mean()
    prev_close = closes.iloc[-2]
    prev_ma5 = ma5.iloc[-2]
    prev_ma10 = ma10.iloc[-2]
    prev_ma20 = ma20.iloc[-2]
    below_all = prev_close <= prev_ma5 and prev_close <= prev_ma10 and prev_close <= prev_ma20
    cur_open = opens.iloc[-1] if hasattr(opens, 'iloc') else closes.iloc[-1] * 0.99
    cur_close = closes.iloc[-1]
    cur_ma5 = ma5.iloc[-1]
    cur_ma10 = ma10.iloc[-1]
    cur_ma20 = ma20.iloc[-1]
    is_yang = cur_close > cur_open
    above_all = cur_close > cur_ma5 and cur_close > cur_ma10 and cur_close > cur_ma20
    if below_all and is_yang and above_all:
        body_pct = (cur_close - cur_open) / cur_open * 100
        if body_pct > 3: return 20
        elif body_pct > 1.5: return 15
        return 10
    return 0

def strategy_mountain_climb(code, closes):
    if len(closes) < 30: return 0
    ma5 = closes.rolling(5).mean()
    ma10 = closes.rolling(10).mean()
    ma20 = closes.rolling(20).mean()
    cur_ma5 = ma5.iloc[-1]
    cur_ma10 = ma10.iloc[-1]
    cur_ma20 = ma20.iloc[-1]
    if not (cur_ma5 > cur_ma10 > cur_ma20): return 0
    ma5_up = ma5.iloc[-1] > ma5.iloc[-5]
    ma10_up = ma10.iloc[-1] > ma10.iloc[-5]
    ma20_up = ma20.iloc[-1] > ma20.iloc[-5]
    above_ma5 = closes.iloc[-1] > cur_ma5
    score = 0
    if ma5_up and ma10_up: score += 8
    if ma20_up: score += 4
    if above_ma5: score += 3
    return min(15, score)


# ==================== 市场环境 ====================
def market_environment():
    try:
        idx = get_index_daily("sh000300", 60)
        if idx is None: return "normal"
        close = idx['close']; ma20 = close.rolling(20).mean()
        change_5d = (close.iloc[-1]-close.iloc[-5])/close.iloc[-5]*100
        if change_5d > 3 and close.iloc[-1] > ma20.iloc[-1]: return "bull"
        if change_5d < -3 or close.iloc[-1] < ma20.iloc[-1]*0.95: return "bear"
        return "normal"
    except: return "normal"


def get_market_indicator_match():
    env = market_environment()
    idx = get_index_daily("sh000300", 30)
    volatility = "normal"
    if idx is not None and len(idx) >= 20:
        vol = idx['returns'].std() * (252 ** 0.5)
        if vol > 0.25: volatility = "high"
        elif vol > 0.18: volatility = "elevated"

    match = {"environment": env, "volatility": volatility, "weight_adjustments": {}, "advice": ""}
    adjustments = {}

    if env == "bull":
        adjustments.update({"均线金叉": 1.3, "ADX趋势": 1.3, "MACD": 1.3, "多头趋势": 1.3, "蛟龙出海": 1.2})
        adjustments.update({"RSI": 0.7, "布林带": 0.7, "KDJ": 0.7})
        match["advice"] = "牛市环境：趋势类指标（MA/ADX/MACD）增权30%，震荡类指标降权"
    elif env == "bear":
        adjustments.update({"RSI超卖": 1.3, "布林下轨": 1.3, "统计超卖": 1.3, "缠论底分型": 1.2})
        adjustments.update({"均线金叉": 0.6, "多头趋势": 0.6, "蛟龙出海": 0.7})
        match["advice"] = "熊市环境：反转类指标（RSI超卖/布林下轨）增权30%，追涨类降权"
    else:
        adjustments.update({"布林带": 1.3, "KDJ": 1.3, "RSI": 1.2, "波浪理论": 1.2})
        adjustments.update({"均线金叉": 0.6, "多头趋势": 0.7, "ADX趋势": 0.7})
        match["advice"] = "震荡环境：均值回归类指标（布林/KDJ）增权30%，趋势类降权"

    if volatility in ("high", "elevated"):
        adjustments.update({"ATR": 1.2, "振幅": 1.2, "波动率": 1.2})
        adjustments.update({"均线金叉": 0.6, "多头趋势": 0.5})
        match["advice"] += "；高波动期额外增权波动率指标"

    match["weight_adjustments"] = adjustments
    return match


def detect_indicator_cycle(factor_name, lookback=252):
    now = datetime.datetime.now()
    cached = INDICATOR_CYCLE_CACHE.get(factor_name)
    if cached and (now - cached.get('checked_at', now)).seconds < 3600:
        return cached

    fmeta = FACTOR_REGISTRY.get(factor_name)
    if not fmeta or not fmeta.get('active'):
        result = {"status": "unknown", "ic_mean": 0, "recommendation": "因子未注册或未激活"}
        INDICATOR_CYCLE_CACHE[factor_name] = {**result, 'checked_at': now}
        return result

    try:
        pool = _get_pool_snapshot()
        if pool is None:
            result = {"status": "error", "ic_mean": 0, "recommendation": "无法获取股票池（非交易日或网络异常）"}
            INDICATOR_CYCLE_CACHE[factor_name] = {**result, 'checked_at': now}
            return result
        pool = pool[~pool['名称'].str.contains('ST|退')]
        pool = pool[pool['总市值'] > 80e8]
        sample_codes = pool['代码'].head(60).tolist()

        rolling_ics = []
        for code in sample_codes:
            df = get_stock_daily_cached(code, lookback + 30)
            if df is None or len(df) < 60: continue
            fdata = load_factor_data(code)
            if not fdata or factor_name not in fdata: continue
            closes = df['close']
            fv = fdata[factor_name]
            fwd_ret = (closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5]
            rolling_ics.append(float(fv) * float(fwd_ret))

        if len(rolling_ics) < 20:
            result = {"status": "unknown", "ic_mean": 0, "recommendation": "样本不足"}
            INDICATOR_CYCLE_CACHE[factor_name] = {**result, 'checked_at': now}
            return result

        ic_mean = np.mean(rolling_ics)
        if ic_mean > 0.03:
            result = {"status": "effective", "ic_mean": round(float(ic_mean), 4),
                      "recommendation": "因子处于有效期，建议加大权重"}
        elif ic_mean < -0.03:
            result = {"status": "reversed", "ic_mean": round(float(ic_mean), 4),
                      "recommendation": "因子处于反转期，建议反转信号方向"}
        elif abs(ic_mean) < 0.01:
            result = {"status": "ineffective", "ic_mean": round(float(ic_mean), 4),
                      "recommendation": "因子处于失效期，建议降低权重或暂时禁用"}
        else:
            result = {"status": "transitional", "ic_mean": round(float(ic_mean), 4),
                      "recommendation": "因子处于过渡期，保持当前权重观察"}

        INDICATOR_CYCLE_CACHE[factor_name] = {**result, 'checked_at': now}
        return result
    except Exception as e:
        result = {"status": "error", "ic_mean": 0, "recommendation": f"检测异常: {str(e)[:60]}"}
        INDICATOR_CYCLE_CACHE[factor_name] = {**result, 'checked_at': now}
        return result


# ==================== 综合评分引擎 ====================
def calculate_comprehensive_score(code):
    df = get_stock_daily_cached(code, 60)
    if df is None or len(df)<20: return None
    closes = df['close']; highs = df['high']; lows = df['low']
    rsi = compute_rsi(closes)
    adx_val, plus_di, minus_di = compute_adx(highs,lows,closes)
    macd_line,_,macd_hist = compute_macd(closes)
    k,_,_ = compute_kdj(highs,lows,closes)
    mom5 = (closes.iloc[-1]-closes.iloc[-5])/closes.iloc[-5]*100
    vol_ratio = df['volume_ratio'].iloc[-1] if 'volume_ratio' in df.columns else 1.0
    rm = closes.rolling(20).mean(); rs = closes.rolling(20).std()
    bl = rm - 2*rs; bu = rm + 2*rs
    bp = (closes.iloc[-1]-bl.iloc[-1])/(bu.iloc[-1]-bl.iloc[-1]) if bu.iloc[-1]!=bl.iloc[-1] else 0.5
    idx_df = get_index_daily()
    if idx_df is not None:
        sr = closes.pct_change().dropna(); ir = idx_df['returns'].dropna()
        clen = min(len(sr),len(ir))
        if clen>=30:
            y = sr.values[-clen:]; x = ir.values[-clen:]
            slope, intercept, r_val, _, _ = stats.linregress(x,y)
            resid = y - (intercept+slope*x)
            resid_z = (resid[-1]-np.mean(resid))/np.std(resid) if np.std(resid)!=0 else 0
        else: resid_z, r_val = 0,0.5
    else: resid_z, r_val = 0,0.5
    info = get_stock_info(code); sectors = get_stock_sector(code)
    factor_stats = load_json(FACTOR_STATS_FILE, {})

    global WEIGHT_LAST_UPDATE
    if WEIGHT_LAST_UPDATE is None or (datetime.datetime.now() - WEIGHT_LAST_UPDATE).days >= 5:
        try: update_weights_internal()
        except: pass

    w = lambda name: factor_stats.get(name, {}).get('weight', FACTOR_REGISTRY.get(name, {}).get('weight', 1.0))
    market_match = get_market_indicator_match()
    mkt_adj = market_match.get('weight_adjustments', {})

    def get_adjusted_weight(name, default_mult=1.0):
        base_w = w(name)
        env_mult = mkt_adj.get(name, 1.0)
        cycle = detect_indicator_cycle(name) if name in FACTOR_REGISTRY else None
        if cycle and cycle.get('status') == 'effective': env_mult *= 1.5
        elif cycle and cycle.get('status') in ('ineffective', 'reversed'): env_mult *= 0.3
        return base_w * env_mult * default_mult

    MKT_MAP = {'ma_cross': '均线金叉', 'chan_theory': '缠论底分型', 'wave_theory': '波浪理论',
               'bull_trend': '多头趋势', 'hot_topic': '热点题材', 'event_driven': '事件驱动',
               'growth_quality': '成长质量', 'revaluation': '预期重估',
               'dragon_rising': '蛟龙出海', 'mountain_climb': '上山爬坡', 'resid_z': '统计超卖'}

    s_ma = strategy_ma_cross(code, closes)
    s_chan = strategy_chan_theory(code, highs, lows, closes)
    s_wave = strategy_wave_theory(code, highs, lows, closes)
    s_bull = strategy_bull_trend(code, closes, adx_val, plus_di, minus_di)
    s_topic = strategy_hot_topic(code, sectors)
    s_event = strategy_event_driven(code)
    s_growth = strategy_growth_quality(code, info)
    s_reval = strategy_revaluation(code, info)
    s_dragon = strategy_dragon_rising(code, closes, highs, lows, df['open'] if 'open' in df.columns else closes)
    s_climb = strategy_mountain_climb(code, closes)

    score = 0
    reasons = []

    if resid_z < -1.5: adj = get_adjusted_weight('resid_z'); score += int(25*adj); reasons.append("统计超卖" + (f"(+30%)" if mkt_adj.get('统计超卖',1.0)>1.1 else ""))
    adj_ma = get_adjusted_weight('ma_cross'); score += int(s_ma*adj_ma)
    if s_ma>=15: reasons.append("均线金叉" + (f"(+30%)" if mkt_adj.get('均线金叉',1.0)>1.1 else ""))
    adj_chan = get_adjusted_weight('chan_theory'); score += int(s_chan*adj_chan)
    if s_chan>=10: reasons.append("缠论底分型" + (f"(+30%)" if mkt_adj.get('缠论底分型',1.0)>1.1 else ""))
    adj_wave = get_adjusted_weight('wave_theory'); score += int(s_wave*adj_wave)
    if s_wave>=10: reasons.append("波浪回调到位")
    adj_bull = get_adjusted_weight('bull_trend'); score += int(s_bull*adj_bull)
    if s_bull>=15: reasons.append("多头趋势" + (f"(+30%)" if mkt_adj.get('多头趋势',1.0)>1.1 else ""))
    adj_topic = get_adjusted_weight('hot_topic'); score += int(s_topic*adj_topic)
    if s_topic>=10: reasons.append("热点题材")
    adj_event = get_adjusted_weight('event_driven'); score += int(s_event*adj_event)
    if s_event>=10: reasons.append("事件驱动")
    adj_growth = get_adjusted_weight('growth_quality'); score += int(s_growth*adj_growth)
    if s_growth>=10: reasons.append("成长质量")
    adj_reval = get_adjusted_weight('revaluation'); score += int(s_reval*adj_reval)
    if s_reval>=10: reasons.append("预期重估")
    adj_dragon = get_adjusted_weight('dragon_rising'); score += int(s_dragon*adj_dragon)
    if s_dragon>=15: reasons.append("蛟龙出海" + (f"(+30%)" if mkt_adj.get('蛟龙出海',1.0)>1.1 else ""))
    elif s_dragon>=10: reasons.append("蛟龙出海(弱)")
    adj_climb = get_adjusted_weight('mountain_climb'); score += int(s_climb*adj_climb)
    if s_climb>=12: reasons.append("上山爬坡")
    elif s_climb>=8: reasons.append("均线多头排列")

    if 30<=rsi<=50: score += 10; reasons.append("RSI健康" + (f"(+30%)" if mkt_adj.get('RSI',1.0)>1.1 else ""))
    if 0.1<bp<0.4: score += 10; reasons.append("布林下轨" + (f"(+30%)" if mkt_adj.get('布林下轨',1.0)>1.1 else ""))
    if abs(r_val)<0.3: score = int(score*0.6)

    score = _apply_dynamic_factors(code, df, score, reasons)
    position_pct = 30 if score>=80 else (20 if score>=65 else (10 if score>=50 else 5))

    return {
        'code':code, 'name':'', 'price':0, 'change_pct':0,
        'signal': min(round(score),100),
        'rsi':round(rsi,2), 'adx':round(adx_val,2) if adx_val else 0,
        'macd_hist':round(macd_hist,4), 'kdj_k':round(k,2),
        'resid_z':round(resid_z,2), 'bb_position':round(bp,2),
        'momentum_5d':round(mom5,2), 'volume_ratio':round(vol_ratio,2),
        'priority_reason': ' + '.join(reasons[:4]) if reasons else '多因子共振',
        'position_advice': f"建议仓位{position_pct}%",
        'strategy_scores':{'均线金叉':s_ma,'缠论':s_chan,'波浪理论':s_wave,'多头趋势':s_bull,'热点题材':s_topic,'事件驱动':s_event,'成长质量':s_growth,'预期重估':s_reval,'蛟龙出海':s_dragon,'上山爬坡':s_climb},
        'basic_info': info, 'sectors': sectors
    }


def _apply_dynamic_factors(code, df, base_score, reasons_list):
    if not DYNAMIC_FACTORS: return base_score
    score = base_score
    for factor_name, factor_code in DYNAMIC_FACTORS.items():
        try:
            safe_globals = {'pd': pd, 'np': np, '__builtins__': {}}
            safe_locals = {}
            exec(factor_code, safe_globals, safe_locals)
            factor_func = safe_locals.get(factor_name)
            if factor_func and df is not None:
                dynamic_score = factor_func(df)
                if dynamic_score is not None and np.isfinite(float(dynamic_score)):
                    perf = FACTOR_PERFORMANCE.get(factor_name, {})
                    ic = perf.get('ic', 0)
                    weight = max(0.3, min(2.0, abs(ic) * 30))
                    score += int(float(dynamic_score) * weight)
                    reasons_list.append(f"AI因子:{factor_name}(IC={ic:.3f})")
        except:
            pass
    return score


def update_weights_internal():
    global WEIGHT_LAST_UPDATE
    updated_count = 0
    for fname, fmeta in FACTOR_REGISTRY.items():
        if not fmeta.get('active'): continue
        ic_val = abs(fmeta.get('ic_30d', 0))
        old_weight = fmeta.get('weight', 1.0)
        new_weight = max(0.1, min(3.0, ic_val * 20 + 0.1))
        if ic_val < 0.02: new_weight = 0.1
        fmeta['weight'] = round(new_weight, 4)
        if abs(new_weight - old_weight) > 0.01: updated_count += 1

    strategy_stats = load_json(FACTOR_STATS_FILE, {})
    trades = load_trades()
    for t in trades[-50:]:
        code = t['code']; profit = t.get('profit', 0)
        for factor in ['ma_cross', 'chan_theory', 'bull_trend', 'resid_z', 'dragon_rising', 'mountain_climb']:
            if factor not in strategy_stats:
                strategy_stats[factor] = {'wins': 0, 'total': 0, 'weight': 1.0}
            strategy_stats[factor]['total'] += 1
            if profit > 0: strategy_stats[factor]['wins'] += 1
            wr = strategy_stats[factor]['wins'] / strategy_stats[factor]['total'] if strategy_stats[factor]['total'] > 0 else 0.5
            strategy_stats[factor]['weight'] = max(0.3, min(3.0, wr * 3))
    save_json(strategy_stats, FACTOR_STATS_FILE)
    save_registry()
    WEIGHT_LAST_UPDATE = datetime.datetime.now()
    return updated_count


def load_positions():
    return load_json(POSITION_FILE, [])

def save_positions(data):
    save_json(data, POSITION_FILE)

def load_trades():
    return load_json(TRADE_FILE, [])

def save_trades(data):
    save_json(data, TRADE_FILE)


def validate_factor_code(code_str, base_factors):
    """验证AI生成的因子代码安全性"""
    forbidden = ['__import__', 'open(', 'exec(', 'eval(', 'subprocess',
                 'os.', 'sys.', 'shutil', 'requests', 'socket', 'urllib']
    for token in forbidden:
        if token in code_str:
            return False, f"因子代码包含禁止内容: {token}"
    if 'return' not in code_str:
        return False, "因子代码必须包含 return 语句"
    return True, "安全"


def backtest_factor_ic(factor_code, factor_name, lookback=60):
    """评估候选因子的IC值（Spearman秩相关）"""
    try:
        pool = _get_pool_snapshot()
        if pool is None:
            return None
        pool = pool[~pool['名称'].str.contains('ST|退')]
        pool = pool[pool['总市值'] > 80e8]
        pool = pool[pool['最新价'] > 5]
        sample_codes = pool['代码'].head(50).tolist()

        safe_globals = {'pd': pd, 'np': np, '__builtins__': {}}
        safe_locals = {}
        exec(factor_code, safe_globals, safe_locals)
        factor_func = safe_locals.get(factor_name)
        if not factor_func:
            return None

        factor_vals = []
        forward_rets = []
        for code in sample_codes:
            df = get_stock_daily_cached(code, lookback)
            if df is None or len(df) < 20:
                continue
            try:
                val = factor_func(df)
                if val is None or (isinstance(val, (pd.Series, np.ndarray)) and len(val) == 0):
                    continue
                v = float(val.iloc[0] if hasattr(val, 'iloc') else val)
                if not np.isfinite(v):
                    continue
                factor_vals.append(v)
                fwd_ret = float((df['close'].iloc[-1] / df['close'].iloc[-5] - 1) if len(df) >= 5 else 0)
                forward_rets.append(fwd_ret)
            except:
                continue

        if len(factor_vals) < 20:
            return None

        ic, _ = stats.spearmanr(factor_vals, forward_rets)
        return float(ic) if np.isfinite(ic) else None
    except Exception:
        return None


def _attribute_trade_to_factors(trade):
    """平仓后将盈亏归因到各策略/因子，更新胜率和权重"""
    code = trade.get('code', '')
    profit = trade.get('profit', 0)
    is_win = profit > 0
    try:
        df = get_stock_daily_cached(code, 60)
        if df is None or len(df) < 20:
            return
        closes = df['close']
        highs = df['high'] if 'high' in df.columns else closes
        lows = df['low'] if 'low' in df.columns else closes

        score_map = {}
        try:
            score_map['ma_cross'] = strategy_ma_cross(code, closes)
            score_map['bull_trend'] = strategy_bull_trend(code, closes)
            score_map['mountain_climb'] = strategy_mountain_climb(code, closes)
            score_map['dragon_rising'] = strategy_dragon_rising(code, closes, highs, lows, closes)
            score_map['wave_theory'] = strategy_wave_theory(code, highs, lows, closes)
            score_map['growth_quality'] = strategy_growth_quality(code)
            score_map['revaluation'] = strategy_revaluation(code)
            score_map['chan_theory'] = strategy_chan_theory(code, highs, lows, closes)
            score_map['hot_topic'] = strategy_hot_topic(code)
            score_map['event_driven'] = strategy_event_driven(code)
        except:
            pass

        factor_stats = load_json(FACTOR_STATS_FILE, {})
        for name, score in score_map.items():
            if score <= 0:
                continue
            if name not in factor_stats:
                factor_stats[name] = {'wins': 0, 'total': 0, 'weight': 1.0}
            factor_stats[name]['total'] += 1
            if is_win:
                factor_stats[name]['wins'] += 1
            t = factor_stats[name]['total']
            w = factor_stats[name]['wins']
            factor_stats[name]['win_rate'] = round(w / t * 100, 1) if t > 0 else 0
            factor_stats[name]['weight'] = max(0.3, min(3.0, (w / t) * 3 if t > 0 else 1.0))

        save_json(factor_stats, FACTOR_STATS_FILE)
    except Exception:
        pass
