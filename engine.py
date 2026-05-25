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
        news = get_global_news_cached()
        if news is None:
            return 0
        news = news.head(20).to_dict('records')
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

# ==================== 新增七大策略因子 ====================

def strategy_boll_squeeze(closes, highs, lows):
    """布林带收窄突破：带窄后放量突破上轨"""
    if len(closes) < 20: return 0
    rm = closes.rolling(20).mean(); rs = closes.rolling(20).std()
    bl = rm - 2*rs; bu = rm + 2*rs
    bw = (bu - bl) / rm  # 带宽
    bw_10d_ago = (bu.iloc[-10] - bl.iloc[-10]) / rm.iloc[-10] if len(closes) >= 30 else 1
    cur = closes.iloc[-1]; cur_bu = bu.iloc[-1]
    # 带子从窄变宽 + 价格突破上轨
    if bw_10d_ago < 0.05 and bw.iloc[-1] > bw_10d_ago * 1.3:
        if cur > cur_bu * 0.98: return 20
        if cur > rm.iloc[-1]: return 10
    return 0

def strategy_volume_price(code, closes, volumes):
    """量价配合：温和放量上涨，量价关系健康"""
    if len(closes) < 20: return 0
    vol_ma5 = volumes.rolling(5).mean(); vol_ma20 = volumes.rolling(20).mean()
    price_up = closes.iloc[-1] > closes.iloc[-5]
    vol_expand = vol_ma5.iloc[-1] > vol_ma20.iloc[-1] * 1.2
    vol_healthy = vol_ma5.iloc[-1] < vol_ma20.iloc[-1] * 3  # 非爆量
    score = 0
    if price_up and vol_expand and vol_healthy: score += 15
    if closes.iloc[-1] > closes.iloc[-10]: score += 5
    return min(20, score)

def strategy_golden_cross_triple(closes, highs, lows):
    """三金叉共振：MA金叉 + MACD金叉 + KDJ金叉"""
    if len(closes) < 26: return 0
    ma5 = closes.rolling(5).mean(); ma10 = closes.rolling(10).mean()
    ma_golden = ma5.iloc[-2] <= ma10.iloc[-2] and ma5.iloc[-1] > ma10.iloc[-1]
    ef = closes.ewm(span=12).mean(); es = closes.ewm(span=26).mean()
    dif = ef - es; dea = dif.ewm(span=9).mean()
    macd_golden = dif.iloc[-2] <= dea.iloc[-2] and dif.iloc[-1] > dea.iloc[-1]
    lowest = lows.rolling(9).min(); highest = highs.rolling(9).max()
    rsv = (closes - lowest) / (highest - lowest) * 100
    k = rsv.ewm(com=2).mean(); d = k.ewm(com=2).mean()
    kdj_golden = k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1]
    count = sum([ma_golden, macd_golden, kdj_golden])
    if count >= 3: return 25
    if count >= 2: return 15
    if count >= 1: return 8
    return 0

def strategy_oversold_reversal(closes, highs, lows):
    """超跌反弹：RSI超卖 + 底背离 + KDJ低位金叉"""
    if len(closes) < 20: return 0
    rsi = compute_rsi(closes)
    k, d, j = compute_kdj(highs, lows, closes)
    # RSI超卖区
    rsi_score = 10 if rsi < 30 else (5 if rsi < 40 else 0)
    # KDJ低位
    kdj_score = 10 if j < 0 else (5 if j < 20 else 0)
    # 底背离：价格新低但RSI不创新低
    divergence = 0
    if len(closes) >= 15:
        price_5d_low = closes.iloc[-5:].min()
        price_10d_low = closes.iloc[-15:-5].min()
        if price_5d_low < price_10d_low and rsi > 30:
            divergence = 10
    return min(25, rsi_score + kdj_score + divergence)

def strategy_momentum_breakout(highs, closes):
    """强势动量：创N日新高 + 均线多头排列"""
    if len(closes) < 60: return 0
    high_20d = highs.iloc[-20:].max()
    high_60d = highs.iloc[-60:].max()
    cur = closes.iloc[-1]
    ma5 = closes.rolling(5).mean(); ma20 = closes.rolling(20).mean(); ma60 = closes.rolling(60).mean()
    score = 0
    if cur >= high_20d * 0.98: score += 10  # 接近20日新高
    if cur >= high_60d * 0.98: score += 5   # 接近60日新高
    if ma5.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]: score += 10  # 多头排列
    return min(25, score)

def strategy_low_vol_breakout(closes, highs, lows):
    """低波动突破：长期横盘后突破"""
    if len(closes) < 30: return 0
    # 前20天振幅小
    amplitude_20d = ((highs.iloc[-20:] - lows.iloc[-20:]) / closes.iloc[-20:]).mean()
    cur = closes.iloc[-1]; ma20 = closes.rolling(20).mean().iloc[-1]
    if amplitude_20d < 0.03:  # 振幅<3%，横盘整理
        if cur > ma20 * 1.02: return 20  # 放量突破
        if cur > ma20: return 10
    return 0

def strategy_consecutive_yang(closes, highs, lows, opens):
    """连阳蓄势：连续小阳线后加速"""
    if len(closes) < 10: return 0
    if not hasattr(opens, 'iloc') or len(opens) < 7: return 0
    yang_count = 0; total_body = 0
    for i in range(-1, -8, -1):
        if closes.iloc[i] > opens.iloc[i]:
            body = (closes.iloc[i] - opens.iloc[i]) / opens.iloc[i]
            total_body += body
            yang_count += 1
        else: break
    if yang_count >= 5 and total_body > 0.02: return 20  # 5连阳+
    if yang_count >= 3 and total_body > 0.01: return 12
    if yang_count >= 2: return 6
    return 0


# ==================== 市场环境 ====================
def quick_prescreen(code):
    """快速预筛选：仅用日线缓存数据，不调 akshare 基本面/板块接口，0.3s内完成"""
    df = get_stock_daily_cached(code, 60)
    if df is None or len(df) < 20:
        return {'code': code, 'prescore': 0}

    closes = df['close']
    volumes = df['volume']
    highs = df['high']
    lows = df['low']

    # RSI
    rsi = compute_rsi(closes)

    # 量比
    vol_ma20 = volumes.rolling(20).mean()
    vol_ratio = float(volumes.iloc[-1] / vol_ma20.iloc[-1]) if vol_ma20.iloc[-1] > 0 else 1.0

    # 5日动量
    mom5 = (closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5] * 100

    # 价格相对20日均线位置
    ma20 = closes.rolling(20).mean()
    price_vs_ma20 = (closes.iloc[-1] - ma20.iloc[-1]) / ma20.iloc[-1] * 100

    # 振幅（剔除僵尸股）
    amplitude = ((highs.iloc[-5:] - lows.iloc[-5:]) / closes.iloc[-5:]).mean() * 100

    # 综合预评分
    score = 0

    # RSI: 30-55 最佳区间
    if 30 <= rsi <= 55:
        score += 30
    elif 25 <= rsi <= 65:
        score += 15
    elif rsi < 25:
        score += 10  # 极度超卖可能有反弹
    else:
        score += 0   # RSI过高，先不加分

    # 动量: 轻微回调或温和上涨最佳
    if -3 <= mom5 <= 5:
        score += 25
    elif 5 < mom5 <= 10:
        score += 15
    elif -8 <= mom5 < -3:
        score += 10  # 超跌反弹候选
    else:
        score += 0

    # 量比: 活跃但不异常
    if 0.8 <= vol_ratio <= 2.5:
        score += 20
    elif 2.5 < vol_ratio <= 4:
        score += 10
    else:
        score += 0

    # 均线位置: 接近或在MA20上方不远
    if -3 <= price_vs_ma20 <= 5:
        score += 15
    elif -8 <= price_vs_ma20 < -3:
        score += 8
    else:
        score += 0

    # 振幅: 有波动但不是异常
    if 2 <= amplitude <= 8:
        score += 10
    elif 0.5 <= amplitude < 2:
        score += 5
    else:
        score += 0

    return {
        'code': code,
        'prescore': min(score, 100),
        'rsi': round(rsi, 1),
        'vol_ratio': round(vol_ratio, 2),
        'mom5': round(mom5, 2),
        'price_vs_ma20': round(price_vs_ma20, 2)
    }


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
               'dragon_rising': '蛟龙出海', 'mountain_climb': '上山爬坡', 'resid_z': '统计超卖',
               'boll_squeeze': '布林突破', 'volume_price': '量价配合',
               'golden_cross_triple': '三金叉共振', 'oversold_reversal': '超跌反转',
               'momentum_breakout': '动量突破', 'low_vol_breakout': '低波突破',
               'consecutive_yang': '连阳蓄势'}

    volumes = df['volume'] if 'volume' in df.columns else closes
    opens = df['open'] if 'open' in df.columns else closes

    s_ma = strategy_ma_cross(code, closes)
    s_chan = strategy_chan_theory(code, highs, lows, closes)
    s_wave = strategy_wave_theory(code, highs, lows, closes)
    s_bull = strategy_bull_trend(code, closes, adx_val, plus_di, minus_di)
    s_topic = strategy_hot_topic(code, sectors)
    s_event = strategy_event_driven(code)
    s_growth = strategy_growth_quality(code, info)
    s_reval = strategy_revaluation(code, info)
    s_dragon = strategy_dragon_rising(code, closes, highs, lows, opens)
    s_climb = strategy_mountain_climb(code, closes)
    s_boll = strategy_boll_squeeze(closes, highs, lows)
    s_vol_price = strategy_volume_price(code, closes, volumes)
    s_triple = strategy_golden_cross_triple(closes, highs, lows)
    s_oversold = strategy_oversold_reversal(closes, highs, lows)
    s_momentum = strategy_momentum_breakout(highs, closes)
    s_lowvol = strategy_low_vol_breakout(closes, highs, lows)
    s_yang = strategy_consecutive_yang(closes, highs, lows, opens)

    score = 0
    reasons = []

    # --- 原有因子 ---
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

    # --- 新增因子 ---
    adj_boll = get_adjusted_weight('boll_squeeze'); score += int(s_boll*adj_boll)
    if s_boll>=15: reasons.append("布林突破")
    adj_vol_price = get_adjusted_weight('volume_price'); score += int(s_vol_price*adj_vol_price)
    if s_vol_price>=15: reasons.append("量价配合")
    adj_triple = get_adjusted_weight('golden_cross_triple'); score += int(s_triple*adj_triple)
    if s_triple>=15: reasons.append("三金叉共振")
    adj_oversold = get_adjusted_weight('oversold_reversal'); score += int(s_oversold*adj_oversold)
    if s_oversold>=15: reasons.append("超跌反转")
    adj_mom = get_adjusted_weight('momentum_breakout'); score += int(s_momentum*adj_mom)
    if s_momentum>=15: reasons.append("动量突破")
    adj_lowvol = get_adjusted_weight('low_vol_breakout'); score += int(s_lowvol*adj_lowvol)
    if s_lowvol>=15: reasons.append("低波突破")
    adj_yang = get_adjusted_weight('consecutive_yang'); score += int(s_yang*adj_yang)
    if s_yang>=15: reasons.append("连阳蓄势")

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
        'strategy_scores':{'均线金叉':s_ma,'缠论':s_chan,'波浪理论':s_wave,'多头趋势':s_bull,'热点题材':s_topic,'事件驱动':s_event,'成长质量':s_growth,'预期重估':s_reval,'蛟龙出海':s_dragon,'上山爬坡':s_climb,'布林突破':s_boll,'量价配合':s_vol_price,'三金叉共振':s_triple,'超跌反转':s_oversold,'动量突破':s_momentum,'低波突破':s_lowvol,'连阳蓄势':s_yang},
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
    """综合IC + 实盘胜率 + 盈亏幅度，每日/每笔交易后更新因子权重"""
    global WEIGHT_LAST_UPDATE
    updated_count = 0
    strategy_stats = load_json(FACTOR_STATS_FILE, {})

    # 从历史交易中学习每个策略的实战表现
    trades = load_trades()
    for t in trades[-100:]:
        code = t['code']
        profit = t.get('profit', 0)
        profit_pct = t.get('profit_pct', 0)

        # 盈亏幅度权重：大涨的交易给予更高权重
        pnl_weight = 1.0
        if profit_pct >= 10: pnl_weight = 2.0
        elif profit_pct >= 5: pnl_weight = 1.5
        elif profit_pct >= 2: pnl_weight = 1.2
        elif profit_pct <= -7: pnl_weight = 0.1
        elif profit_pct <= -3: pnl_weight = 0.5

        for factor in ['ma_cross', 'chan_theory', 'bull_trend', 'resid_z',
                        'dragon_rising', 'mountain_climb', 'wave_theory',
                        'growth_quality', 'revaluation', 'hot_topic', 'event_driven',
                        'boll_squeeze', 'volume_price', 'golden_cross_triple',
                        'oversold_reversal', 'momentum_breakout', 'low_vol_breakout',
                        'consecutive_yang']:
            if factor not in strategy_stats:
                strategy_stats[factor] = {'wins': 0, 'total': 0, 'weight': 1.0, 'total_profit': 0}
            strategy_stats[factor]['total'] += 1
            if profit > 0:
                strategy_stats[factor]['wins'] += pnl_weight
            else:
                strategy_stats[factor]['wins'] += (1 - pnl_weight) * 0.3
            strategy_stats[factor]['total_profit'] = strategy_stats[factor].get('total_profit', 0) + profit * pnl_weight

    # 综合 IC（历史回测） + 实战胜率 计算最终权重
    for fname, fmeta in FACTOR_REGISTRY.items():
        if not fmeta.get('active'):
            continue
        ic_30d = fmeta.get('ic_30d', None)
        old_weight = fmeta.get('weight', 1.0)

        # IC 贡献：有IC数据才参与，未计算的默认给 1.0（中性）
        if ic_30d is not None and ic_30d != 0:
            ic_weight = max(0.3, min(2.0, abs(ic_30d) * 25))
        else:
            ic_weight = 1.0

        # 实战贡献：从交易记录中获取该因子的胜率权重
        strategy_name = fmeta.get('strategy_name', '')
        trade_weight = 1.0
        if strategy_name and strategy_name in strategy_stats:
            ss = strategy_stats[strategy_name]
            t = ss['total']
            if t > 0:
                wr = ss['wins'] / t
                trade_weight = max(0.1, min(3.0, wr * 3))

        # 最终权重 = IC权重 × 0.4 + 实战权重 × 0.6
        new_weight = ic_weight * 0.4 + trade_weight * 0.6
        new_weight = max(0.3, min(3.0, new_weight))

        # 长期无交易的因子逐步衰减（仅对策略因子）
        if strategy_name and strategy_name in strategy_stats:
            if strategy_stats[strategy_name]['total'] < 3:
                new_weight *= 0.7
                new_weight = max(0.3, new_weight)

        fmeta['weight'] = round(new_weight, 4)
        if abs(new_weight - old_weight) > 0.005:
            updated_count += 1

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
    """平仓后将盈亏归因到各策略/因子，使用盈亏幅度强化权重调整"""
    code = trade.get('code', '')
    profit = trade.get('profit', 0)
    profit_pct = trade.get('profit_pct', 0)
    is_win = profit > 0

    # 盈亏幅度分级：大幅盈利→强奖励，大幅亏损→强惩罚
    if profit_pct >= 10:
        boost_mult = 2.0      # 大涨：重奖贡献因子
    elif profit_pct >= 5:
        boost_mult = 1.5
    elif profit_pct >= 2:
        boost_mult = 1.2
    elif profit_pct >= 0:
        boost_mult = 1.0
    elif profit_pct >= -3:
        boost_mult = 0.7      # 小亏：轻罚
    elif profit_pct >= -7:
        boost_mult = 0.4      # 中亏：重罚
    else:
        boost_mult = 0.1      # 大亏：几乎清零该因子贡献

    try:
        df = get_stock_daily_cached(code, 60)
        if df is None or len(df) < 20:
            return
        closes = df['close']
        highs = df['high'] if 'high' in df.columns else closes
        lows = df['low'] if 'low' in df.columns else closes
        adx_val, plus_di, minus_di = compute_adx(highs, lows, closes)

        # 计算买入时的因子得分
        volumes = df['volume'] if 'volume' in df.columns else closes
        score_map = {}
        try:
            score_map['ma_cross'] = strategy_ma_cross(code, closes)
            score_map['bull_trend'] = strategy_bull_trend(code, closes, adx_val, plus_di, minus_di)
            score_map['mountain_climb'] = strategy_mountain_climb(code, closes)
            score_map['dragon_rising'] = strategy_dragon_rising(code, closes, highs, lows,
                df['open'] if 'open' in df.columns else closes)
            score_map['wave_theory'] = strategy_wave_theory(code, highs, lows, closes)
            score_map['growth_quality'] = strategy_growth_quality(code, get_stock_info(code))
            score_map['revaluation'] = strategy_revaluation(code, get_stock_info(code))
            score_map['chan_theory'] = strategy_chan_theory(code, highs, lows, closes)
            score_map['hot_topic'] = strategy_hot_topic(code, get_stock_sector(code))
            score_map['event_driven'] = strategy_event_driven(code)
            score_map['boll_squeeze'] = strategy_boll_squeeze(closes, highs, lows)
            score_map['volume_price'] = strategy_volume_price(code, closes, volumes)
            score_map['golden_cross_triple'] = strategy_golden_cross_triple(closes, highs, lows)
            score_map['oversold_reversal'] = strategy_oversold_reversal(closes, highs, lows)
            score_map['momentum_breakout'] = strategy_momentum_breakout(highs, closes)
            score_map['low_vol_breakout'] = strategy_low_vol_breakout(closes, highs, lows)
            score_map['consecutive_yang'] = strategy_consecutive_yang(closes, highs, lows,
                df['open'] if 'open' in df.columns else closes)
        except:
            pass

        factor_stats = load_json(FACTOR_STATS_FILE, {})
        total_active_factors = sum(1 for s in score_map.values() if s > 0)

        for name, score in score_map.items():
            if name not in factor_stats:
                factor_stats[name] = {'wins': 0, 'total': 0, 'weight': 1.0, 'total_profit': 0}

            factor_stats[name]['total'] += 1
            if is_win:
                factor_stats[name]['wins'] += 1
            factor_stats[name]['total_profit'] = factor_stats[name].get('total_profit', 0) + profit

            t = factor_stats[name]['total']
            w = factor_stats[name]['wins']
            base_wr = w / t if t > 0 else 0.5

            # 权重 = 基础胜率 × 盈亏幅度系数，得分越高的因子调整幅度越大
            if score > 0:
                score_weight = min(2.0, score / 15)  # 高分因子受调整影响更大
                new_weight = base_wr * 3 * (0.7 + 0.3 * boost_mult) * score_weight
            else:
                # 未触发的因子：轻微衰减
                new_weight = factor_stats[name].get('weight', 1.0) * 0.95

            factor_stats[name]['weight'] = round(max(0.1, min(3.0, new_weight)), 4)
            factor_stats[name]['win_rate'] = round(base_wr * 100, 1) if t > 0 else 0
            factor_stats[name]['last_trade_pnl'] = round(profit, 2)
            factor_stats[name]['boost'] = round(boost_mult, 2)

        # 同步更新 FACTOR_REGISTRY 中对应因子的权重
        strategy_to_factor = {
            'ma_cross': '均线金叉', 'chan_theory': '缠论底分型', 'wave_theory': '波浪理论',
            'bull_trend': '多头趋势', 'hot_topic': '热点题材', 'event_driven': '事件驱动',
            'growth_quality': '成长质量', 'revaluation': '预期重估',
            'dragon_rising': '蛟龙出海', 'mountain_climb': '上山爬坡',
            'boll_squeeze': '布林突破', 'volume_price': '量价配合',
            'golden_cross_triple': '三金叉共振', 'oversold_reversal': '超跌反转',
            'momentum_breakout': '动量突破', 'low_vol_breakout': '低波突破',
            'consecutive_yang': '连阳蓄势'
        }
        for sname, fname in strategy_to_factor.items():
            if sname in factor_stats and fname in FACTOR_REGISTRY:
                FACTOR_REGISTRY[fname]['weight'] = factor_stats[sname]['weight']
                FACTOR_REGISTRY[fname]['win_rate'] = factor_stats[sname].get('win_rate', 0)

        save_json(factor_stats, FACTOR_STATS_FILE)
        save_registry()
    except Exception:
        pass
