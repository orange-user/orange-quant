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


# ==================== ArrayManager 统一指标计算 ====================
class ArrayManager:
    """统一管理K线数据, 所有指标通过它算 (移植自vnpy)"""
    def __init__(self, size=100):
        self.size = size
        self.inited = False
        self.open = np.zeros(size)
        self.high = np.zeros(size)
        self.low = np.zeros(size)
        self.close = np.zeros(size)
        self.volume = np.zeros(size)

    def update_bar(self, o, h, l, c, v):
        """推进一根K线"""
        self.open = np.roll(self.open, -1)
        self.high = np.roll(self.high, -1)
        self.low = np.roll(self.low, -1)
        self.close = np.roll(self.close, -1)
        self.volume = np.roll(self.volume, -1)
        self.open[-1] = o
        self.high[-1] = h
        self.low[-1] = l
        self.close[-1] = c
        self.volume[-1] = v
        if not self.inited and self.close[0] != 0:
            self.inited = True

    def sma(self, n, array=False):
        result = pd.Series(self.close).rolling(n).mean().values
        if array:
            return result
        return result[-1]

    def ema(self, n, array=False):
        result = pd.Series(self.close).ewm(span=n, adjust=False).mean().values
        if array:
            return result
        return result[-1]

    def macd(self, fast=12, slow=26, signal=9):
        close_series = pd.Series(self.close)
        ema_fast = close_series.ewm(span=fast, adjust=False).mean()
        ema_slow = close_series.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        hist = dif - dea
        return dif.values[-1], dea.values[-1], hist.values[-1]

    def rsi(self, n=14, array=False):
        close_series = pd.Series(self.close)
        delta = close_series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(n).mean()
        avg_loss = loss.rolling(n).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        if array:
            return rsi.values
        return float(rsi.values[-1]) if not pd.isna(rsi.values[-1]) else 50

    def atr(self, n=14, array=False):
        """ATR (Average True Range) 波动率指标, 用于动态止损"""
        high_series = pd.Series(self.high)
        low_series = pd.Series(self.low)
        close_series = pd.Series(self.close)
        tr = pd.concat([
            high_series - low_series,
            (high_series - close_series.shift(1)).abs(),
            (low_series - close_series.shift(1)).abs()
        ], axis=1).max(axis=1)
        result = tr.rolling(n).mean().values
        if array:
            return result
        return result[-1]

    def boll(self, n=20, dev=2):
        close_series = pd.Series(self.close)
        rm = close_series.rolling(n).mean()
        rs = close_series.rolling(n).std()
        upper = rm + rs * dev
        lower = rm - rs * dev
        bp = (close_series - lower) / (upper - lower)
        return upper.values[-1], rm.values[-1], lower.values[-1], bp.values[-1]

    def kdj(self, n=9):
        highest = pd.Series(self.high).rolling(n).max()
        lowest = pd.Series(self.low).rolling(n).min()
        rsv = (pd.Series(self.close) - lowest) / (highest - lowest) * 100
        k = rsv.ewm(com=2).mean()
        d = k.ewm(com=2).mean()
        j = 3 * k - 2 * d
        return k.values[-1], d.values[-1], j.values[-1]

    def cci(self, n=20):
        tp = (pd.Series(self.high) + pd.Series(self.low) + pd.Series(self.close)) / 3
        sma_tp = tp.rolling(n).mean()
        mad = tp.rolling(n).apply(lambda x: np.abs(x - x.mean()).mean())
        cci = (tp - sma_tp) / (0.015 * mad)
        return cci.values[-1] if not pd.isna(cci.values[-1]) else 0


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

def strategy_cdl_pattern(code, closes, highs, lows, opens):
    """TA-Lib K线形态识别 (61种)"""
    try:
        import talib
        o = opens.values.astype(float)
        h = highs.values.astype(float)
        l = lows.values.astype(float)
        c = closes.values.astype(float)
        patterns = {
            'CDL2CROWS': talib.CDL2CROWS(o, h, l, c),
            'CDL3WHITESOLDIERS': talib.CDL3WHITESOLDIERS(o, h, l, c),
            'CDL3BLACKCROWS': talib.CDL3BLACKCROWS(o, h, l, c),
            'CDLMORNINGSTAR': talib.CDLMORNINGSTAR(o, h, l, c),
            'CDLEVENINGSTAR': talib.CDLEVENINGSTAR(o, h, l, c),
            'CDLHAMMER': talib.CDLHAMMER(o, h, l, c),
            'CDLSHOOTINGSTAR': talib.CDLSHOOTINGSTAR(o, h, l, c),
            'CDLENGULFING': talib.CDLENGULFING(o, h, l, c),
            'CDLHARAMI': talib.CDLHARAMI(o, h, l, c),
            'CDLDOJI': talib.CDLDOJI(o, h, l, c),
        }
        score = 0
        for name, result in patterns.items():
            last_val = result[-1]
            if last_val > 0:
                score += 5  # 看涨形态
            elif last_val < 0:
                score -= 3  # 看跌形态
        return max(0, min(25, score + 10))
    except ImportError:
        return 0

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
    """三金叉共振：MA金叉 + MACD金叉 + KDJ金叉 — 分析显示最强冲高预测(+15%)"""
    if len(closes) < 26: return 0
    ma5 = closes.rolling(5).mean(); ma10 = closes.rolling(10).mean()
    ma_golden = ma5.iloc[-2] <= ma10.iloc[-2] and ma5.iloc[-1] > ma10.iloc[-1]
    ma_already = ma5.iloc[-1] > ma10.iloc[-1]  # 已金叉(不要求当天)
    ef = closes.ewm(span=12).mean(); es = closes.ewm(span=26).mean()
    dif = ef - es; dea = dif.ewm(span=9).mean()
    macd_golden = dif.iloc[-2] <= dea.iloc[-2] and dif.iloc[-1] > dea.iloc[-1]
    macd_bull = dif.iloc[-1] > 0 and dif.iloc[-1] > dea.iloc[-1]  # MACD多头
    lowest = lows.rolling(9).min(); highest = highs.rolling(9).max()
    rsv = (closes - lowest) / (highest - lowest) * 100
    k = rsv.ewm(com=2).mean(); d = k.ewm(com=2).mean()
    kdj_golden = k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1]
    kdj_bull = k.iloc[-1] > d.iloc[-1]  # KDJ多头
    count_golden = sum([ma_golden, macd_golden, kdj_golden])
    count_bull = sum([ma_already, macd_bull, kdj_bull])
    # 三金叉同步出现最强
    if count_golden >= 3: return 42
    if count_golden >= 2: return 28
    # 已形成多头排列也有较强信号
    if count_bull >= 3: return 25
    if count_bull >= 2: return 15
    if count_golden >= 1: return 10
    return 0

def strategy_oversold_reversal(closes, highs, lows):
    """超跌反弹（分析显示预测力~0%，已降权）"""
    if len(closes) < 20: return 0
    rsi = compute_rsi(closes)
    k, d, j = compute_kdj(highs, lows, closes)
    rsi_score = 5 if rsi < 30 else (2 if rsi < 40 else 0)
    kdj_score = 5 if j < 0 else (2 if j < 20 else 0)
    divergence = 0
    if len(closes) >= 15:
        price_5d_low = closes.iloc[-5:].min()
        price_10d_low = closes.iloc[-15:-5].min()
        if price_5d_low < price_10d_low and rsi > 30:
            divergence = 5
    return min(15, rsi_score + kdj_score + divergence)

def strategy_momentum_breakout(highs, closes):
    """强势动量：创N日新高 + 均线多头排列（分析显示预测力+6%，已提权）"""
    if len(closes) < 60: return 0
    high_20d = highs.iloc[-20:].max()
    high_60d = highs.iloc[-60:].max()
    cur = closes.iloc[-1]
    ma5 = closes.rolling(5).mean(); ma20 = closes.rolling(20).mean(); ma60 = closes.rolling(60).mean()
    score = 0
    if cur >= high_20d * 0.98: score += 10
    if cur >= high_60d * 0.98: score += 10
    if ma5.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]: score += 12
    if closes.iloc[-1] > ma5.iloc[-1] and ma5.iloc[-1] > ma20.iloc[-1]: score += 8
    return min(30, score)

def strategy_low_vol_breakout(closes, highs, lows):
    """低波动突破（分析显示预测力~0%，已大幅降权）"""
    if len(closes) < 30: return 0
    amplitude_20d = ((highs.iloc[-20:] - lows.iloc[-20:]) / closes.iloc[-20:]).mean()
    cur = closes.iloc[-1]; ma20 = closes.rolling(20).mean().iloc[-1]
    if amplitude_20d < 0.03:
        if cur > ma20 * 1.02: return 8
        if cur > ma20: return 4
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


def strategy_cointegration(code):
    """协整套利：利用同板块配对统计均值回归"""
    try:
        from cointegration import get_cointegration_score
        score, z, _ = get_cointegration_score(code)
        return score
    except Exception:
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

    # RSI: 50-70 最佳区间（分析显示冲高股RSI集中在50-70）
    if 50 <= rsi <= 70:
        score += 30
    elif 30 <= rsi < 50:
        score += 20
    elif 70 < rsi <= 80:
        score += 12
    elif rsi < 25:
        score += 5  # 超卖反弹预测力很差
    else:
        score += 0

    # 动量: 轻微上涨最佳（分析显示冲高股mom5均值为+5.2%）
    if 2 <= mom5 <= 8:
        score += 25
    elif 0 <= mom5 < 2:
        score += 20
    elif 8 < mom5 <= 15:
        score += 12
    elif -5 <= mom5 < 0:
        score += 10
    else:
        score += 0

    # 量比: 活跃但不异常
    if 0.8 <= vol_ratio <= 2.5:
        score += 20
    elif 2.5 < vol_ratio <= 4:
        score += 10
    else:
        score += 0

    # 均线位置: 在MA20上方更强（分析显示71%冲高股已在MA20之上）
    if 3 <= price_vs_ma20 <= 12:
        score += 20
    elif 0 <= price_vs_ma20 < 3:
        score += 15
    elif -5 <= price_vs_ma20 < 0:
        score += 8
    elif price_vs_ma20 >= 12:
        score += 10  # 涨幅过大可能回调
    else:
        score += 0

    # 振幅: 有波动但不是异常
    if 2 <= amplitude <= 8:
        score += 10
    elif 0.5 <= amplitude < 2:
        score += 5
    else:
        score += 0

    # ==== AL Brooks 轻量上下文 (无ADX版, 仅用趋势K线+区间位置) ====
    opens = df['open']
    trend_bars = 0
    for i in range(-1, -6, -1):
        if len(closes) < abs(i): break
        body = abs(closes.iloc[i] - opens.iloc[i])
        rng = highs.iloc[i] - lows.iloc[i]
        if rng <= 0 or body / rng < 0.50: break
        close_pos = (closes.iloc[i] - lows.iloc[i]) / rng
        if closes.iloc[i] >= opens.iloc[i] and close_pos >= 0.50: trend_bars += 1
        elif closes.iloc[i] < opens.iloc[i] and close_pos <= 0.50: trend_bars += 1
        else: break
    range_20d = highs.iloc[-20:].max() - lows.iloc[-20:].min()
    pos_in_range = 0.5
    if range_20d > 0:
        pos_in_range = (closes.iloc[-1] - lows.iloc[-20:].min()) / range_20d
    mid_range = 0.35 <= pos_in_range <= 0.65
    if trend_bars >= 3 and not mid_range:
        score = int(score * 1.10)  # 轻量趋势加成
    elif mid_range and trend_bars < 2:
        score = int(score * 0.90)  # 微幅下调震荡

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


def classify_context_state(df, adx_val, plus_di, minus_di):
    """AL Brooks 背景上下文分类器
    将股票K线背景分类为 TREND / TRADING_RANGE / TRANSITION
    返回: (multiplier, context_label)
      TREND → 1.15, 震荡 → 0.90, 过渡 → 1.00
    """
    if df is None or len(df) < 20:
        return 1.0, 'unknown'

    closes = df['close']; highs = df['high']; lows = df['low']
    opens = df['open']

    # ADX > 25 为趋势行情
    adx_trend = adx_val is not None and adx_val > 25

    # 连续趋势K线判定: 实体 >= 50% + 收盘在自身半区
    trend_bars = 0
    for i in range(-1, -6, -1):
        if len(closes) < abs(i):
            break
        body = abs(closes.iloc[i] - opens.iloc[i])
        rng = highs.iloc[i] - lows.iloc[i]
        if rng <= 0 or body / rng < 0.50:
            break
        close_pos = (closes.iloc[i] - lows.iloc[i]) / rng
        if closes.iloc[i] >= opens.iloc[i] and close_pos >= 0.50:
            trend_bars += 1  # 看涨趋势K线
        elif closes.iloc[i] < opens.iloc[i] and close_pos <= 0.50:
            trend_bars += 1  # 看跌趋势K线
        else:
            break
    consecutive_trend = trend_bars >= 3

    # 20日价格区间位置: 中间30% = 震荡
    range_20d = highs.iloc[-20:].max() - lows.iloc[-20:].min()
    pos_in_range = 0.5
    if range_20d > 0:
        pos_in_range = (closes.iloc[-1] - lows.iloc[-20:].min()) / range_20d
    mid_range = 0.35 <= pos_in_range <= 0.65

    # DI方向
    di_bull = plus_di is not None and minus_di is not None and plus_di > minus_di
    di_bear = plus_di is not None and minus_di is not None and minus_di > plus_di

    # 判定
    is_trend = (adx_trend and consecutive_trend) or (adx_trend and not mid_range)
    is_range = mid_range and not adx_trend

    if is_trend:
        multiplier = 1.15
        context = 'trend_bull' if di_bull else ('trend_bear' if di_bear else 'trend')
    elif is_range:
        multiplier = 0.90
        context = 'trading_range'
    else:
        multiplier = 1.0
        context = 'transition'

    return multiplier, context


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


# ==================== 新移植策略因子 ====================

def strategy_pullback_buy(code, closes, highs, lows):
    """回调买入: 长期上升趋势+短期回调到支撑 (从abu ABuFactorBuyTrend移植)"""
    if len(closes) < 60:
        return 0
    ma20 = closes.rolling(20).mean()
    ma60 = closes.rolling(60).mean()
    cur = closes.iloc[-1]
    cur_ma20 = ma20.iloc[-1]
    cur_ma60 = ma60.iloc[-1]
    # 长期多头趋势 (MA20 > MA60)
    long_bull = cur_ma20 > cur_ma60 * 1.05
    # 短期回调 (最近3天有下跌)
    short_pullback = any(closes.iloc[-i] < closes.iloc[-i-1] for i in range(1, 4))
    # 价格在MA20附近 (回调到支撑)
    near_support = abs(cur - cur_ma20) / cur_ma20 < 0.02
    if long_bull and short_pullback and near_support:
        return 20
    if long_bull and near_support:
        return 10
    return 0


def strategy_dual_thrust(code, closes, highs, lows, opens):
    """DualThrust通道突破: K×昨日振幅算通道 (从vnpy DualThrustStrategy移植)"""
    if len(closes) < 3:
        return 0
    yesterday = -2 if len(closes) >= 2 else -1
    k1, k2 = 0.4, 0.4  # 通道系数
    prev_high = highs.iloc[yesterday]
    prev_low = lows.iloc[yesterday]
    prev_close = closes.iloc[yesterday]
    # 昨日振幅
    day_range = max(prev_high - prev_low, prev_high - prev_close, prev_close - prev_low)
    if day_range <= 0:
        return 0
    open_price = opens.iloc[-1]
    upper = open_price + k1 * day_range
    lower = open_price - k2 * day_range
    cur = closes.iloc[-1]
    if cur > upper:
        return 20  # 突破上轨, 看涨
    elif cur < lower:
        return 15  # 跌破下轨, 看跌(反向)
    return 0


def strategy_king_keltner(code, closes, highs, lows):
    """KingKeltner通道: EMA±ATR×倍数 (从vnpy KingKeltnerStrategy移植)"""
    if len(closes) < 30:
        return 0
    ema = closes.ewm(span=20, adjust=False).mean()
    tr = pd.concat([
        highs - lows,
        (highs - closes.shift(1)).abs(),
        (lows - closes.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(10).mean()
    dev = 1.6  # 倍数
    upper = ema + atr * dev
    lower = ema - atr * dev
    cur = closes.iloc[-1]
    if cur > upper.iloc[-1] and atr.iloc[-1] > 0:
        return 18  # 突破上轨
    elif cur < lower.iloc[-1] and atr.iloc[-1] > 0:
        return 12  # 跌破下轨(反向)
    return 0


def strategy_weekday_effect(code, closes):
    """周几效应: 统计历史盈利的星期几 (从abu ABuFactorBuyWD移植)"""
    try:
        from data import get_stock_daily_cached
        df = get_stock_daily_cached(code, 120)
        if df is None or len(df) < 40:
            return 0
        df['weekday'] = pd.to_datetime(df['date']).dt.weekday
        df['next_ret'] = df['close'].pct_change(-1).shift(-1)
        win_rates = df.groupby('weekday')['next_ret'].apply(lambda x: (x > 0).mean())
        today_wd = datetime.datetime.now().weekday()
        rate = win_rates.get(today_wd, 0.5)
        if rate > 0.55:
            return 12
        elif rate > 0.52:
            return 6
        return 0
    except Exception:
        return 0


def strategy_two_day_yang(code, closes, opens):
    """两连阳加速: 连续2天上涨且第二天加速 (从abu AbuTwoDayBuy移植)"""
    if len(closes) < 5:
        return 0
    if not hasattr(opens, 'iloc') or len(opens) < 5:
        return 0
    d1 = (closes.iloc[-2] - opens.iloc[-2]) / opens.iloc[-2]
    d2 = (closes.iloc[-1] - opens.iloc[-1]) / opens.iloc[-1]
    if d2 > d1 > 0:
        return 15
    if d1 > 0 and d2 > 0:
        return 8
    return 0


def strategy_topk_momentum(code, closes, highs, lows, volumes):
    """Top-K动量: 综合多维度打分 (从vnpy AlphaStrategy启发)"""
    if len(closes) < 20:
        return 0
    score = 0
    # 动量
    mom5 = (closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5] * 100
    if 2 <= mom5 <= 8:
        score += 8
    # RSI
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    if 40 <= rsi.iloc[-1] <= 60:
        score += 7
    # 量比
    vol_ratio = volumes.iloc[-1] / volumes.rolling(20).mean().iloc[-1]
    if 0.8 <= vol_ratio <= 2.5:
        score += 5
    return min(20, score)


# ==================== 新移植策略因子结束 ====================


# ==================== AL Brooks 价格行为函数 ====================

def compute_signal_bar_quality(df):
    """信号K线质量评分 (SBQ) — AL Brooks 8标准
    对最新日K线打分，评估其作为交易信号的"干净程度"
    范围 0-30，缩放到 0-15 加成
    """
    if df is None or len(df) < 3:
        return 0, 'none'

    closes = df['close'].values; opens = df['open'].values
    highs = df['high'].values; lows = df['low'].values
    volumes = df['volume'].values if 'volume' in df.columns else None

    i = -1
    cur_close = closes[i]; cur_open = opens[i]
    cur_high = highs[i]; cur_low = lows[i]
    cur_range = cur_high - cur_low
    if cur_range <= 0:
        return 0, 'none'

    body = abs(cur_close - cur_open)
    body_pct = body / cur_range

    score = 0

    # 1) 实体 >= 50% 范围 → +5(趋势K线)
    if body_pct >= 0.50:
        score += 5

    # 2-3) 上下影线计算
    if cur_close >= cur_open:  # 阳线
        upper_wick = cur_high - cur_close
        lower_wick = cur_open - cur_low
        is_bull = True
    else:  # 阴线
        upper_wick = cur_high - cur_open
        lower_wick = cur_close - cur_low
        is_bull = False

    def safe_pct(wick, rng):
        return wick / rng if rng > 0 else 0.5

    upper_pct = safe_pct(upper_wick, cur_range)
    lower_pct = safe_pct(lower_wick, cur_range)

    if is_bull:
        if upper_pct < 0.25: score += 3    # 上方阻力小
        if lower_pct < 0.25: score += 3    # 下方支撑确认
    else:
        if lower_pct < 0.25: score += 3    # 下方无支撑
        if upper_pct < 0.25: score += 3    # 上方阻力确认

    # 4) 收盘在范围上25%(看涨)/下25%(看跌)
    close_pos = (cur_close - cur_low) / cur_range
    if is_bull:
        if close_pos >= 0.75: score += 5
        elif close_pos >= 0.50: score += 2
    else:
        if close_pos <= 0.25: score += 5
        elif close_pos <= 0.50: score += 2

    # 5) 与前K线重叠最小 → +4
    prev_high = highs[-2]; prev_low = lows[-2]
    overlap = max(0, min(cur_high, prev_high) - max(cur_low, prev_low))
    overlap_pct = overlap / cur_range
    if overlap_pct < 0.20:
        score += 4

    # 6) 反转前K线收盘方向 → +4
    prev_dir = 1 if closes[-2] >= opens[-2] else -1
    cur_dir = 1 if cur_close >= cur_open else -1
    if cur_dir != prev_dir and body_pct > 0.30:
        score += 4

    # 7) 收盘超前2根极值 → +4
    prior_max = max(highs[-2], highs[-3])
    prior_min = min(lows[-2], lows[-3])
    if cur_dir == 1 and cur_close > prior_max:
        score += 4
    elif cur_dir == -1 and cur_close < prior_min:
        score += 4

    # 8) 量比 > 1.2 → +2
    if volumes is not None and len(volumes) >= 20:
        vol_ma20 = volumes[-20:].mean()
        vol_ratio = volumes[-1] / vol_ma20 if vol_ma20 > 0 else 1.0
        if vol_ratio > 1.2:
            score += 2

    if is_bull:
        bar_type = 'bull_strong' if score >= 20 else 'bull_weak'
    else:
        bar_type = 'bear_strong' if score >= 20 else 'bear_weak'
    return min(30, score), bar_type


def detect_inside_outside_bar(df):
    """Inside Bar / Outside Bar 检测 (非talib依赖)
    返回: (type, score_adj)
      type: 'inside' | 'outside_bull' | 'outside_bear' | 'outside_neutral' | 'none'
    """
    if df is None or len(df) < 3:
        return 'none', 0

    cur_high = df['high'].iloc[-1]; cur_low = df['low'].iloc[-1]
    cur_open = df['open'].iloc[-1]; cur_close = df['close'].iloc[-1]
    prev_high = df['high'].iloc[-2]; prev_low = df['low'].iloc[-2]
    prev_open = df['open'].iloc[-2]; prev_close = df['close'].iloc[-2]

    is_inside = cur_high <= prev_high and cur_low >= prev_low
    is_outside = cur_high >= prev_high and cur_low <= prev_low

    if is_inside:
        return 'inside', -3  # 犹豫信号

    if is_outside:
        cur_bull = cur_close > cur_open
        prev_bull = prev_close > prev_open
        if cur_bull and not prev_bull:
            return 'outside_bull', 5
        elif not cur_bull and prev_bull:
            return 'outside_bear', 5
        else:
            return 'outside_neutral', 3

    return 'none', 0


def strategy_three_push(code, closes, highs, lows, volumes):
    """三推反转 (AL Brooks Three Push)
    识别三次同向推动 + 动能衰竭 → 趋势反转信号
    评分 0-25
    """
    if len(closes) < 20:
        return 0

    n = len(closes)
    lookback = min(n, 30)
    h = highs.iloc[-lookback:].values
    lo = lows.iloc[-lookback:].values
    c = closes.iloc[-lookback:].values
    v = volumes.iloc[-lookback:].values if len(volumes) >= lookback else None

    # 找局部高点
    pushes_up = []
    for i in range(1, len(h) - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            pushes_up.append((i, h[i]))

    score = 0

    # 看跌三推顶
    if len(pushes_up) >= 3:
        p1, p2, p3 = pushes_up[-3], pushes_up[-2], pushes_up[-1]
        if p1[1] < p2[1] < p3[1]:  # 高点依次抬高
            idx3 = p3[0]
            # 第三推动作幅
            rng3 = max(h[idx3] - lo[idx3], 0.01)
            body3 = abs(c[idx3] - lo[idx3])
            body_ratio3 = min(body3 / rng3, 1.0)
            # RSI 背离检查
            delta = closes.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_g = gain.rolling(14).mean()
            avg_l = loss.rolling(14).mean()
            rs = avg_g / avg_l
            rsi_s = 100 - (100 / (1 + rs))
            offset_p1 = p1[0]
            offset_p3 = p3[0]
            abs_idx_p1 = len(closes) - lookback + offset_p1
            abs_idx_p3 = len(closes) - lookback + offset_p3
            if 0 <= abs_idx_p1 < len(rsi_s) and 0 <= abs_idx_p3 < len(rsi_s):
                rsi_p1 = rsi_s.iloc[abs_idx_p1]
                rsi_p3 = rsi_s.iloc[abs_idx_p3]
                rsi_div = rsi_p3 < rsi_p1  # 价格新高RSI新低
            else:
                rsi_div = False

            if body_ratio3 < 0.40 and rsi_div:
                score = max(score, 25)  # 实体衰竭 + 背离
            elif body_ratio3 < 0.40:
                score = max(score, 15)  # 仅实体衰竭
            else:
                score = max(score, 10)  # 仅三推结构

    # 找局部低点(看涨三推底)
    pushes_dn = []
    for i in range(1, len(lo) - 1):
        if lo[i] < lo[i - 1] and lo[i] < lo[i + 1]:
            pushes_dn.append((i, lo[i]))

    if len(pushes_dn) >= 3:
        p1, p2, p3 = pushes_dn[-3], pushes_dn[-2], pushes_dn[-1]
        if p1[1] > p2[1] > p3[1]:  # 低点依次降低
            idx3 = p3[0]
            rng3 = max(h[idx3] - lo[idx3], 0.01)
            # 保守实体估算(用low近似open)
            body3 = abs(c[idx3] - lo[idx3])
            body_ratio3 = min(body3 / rng3, 1.0)
            delta = closes.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_g = gain.rolling(14).mean()
            avg_l = loss.rolling(14).mean()
            rs = avg_g / avg_l
            rsi_s = 100 - (100 / (1 + rs))
            offset_p1 = p1[0]; offset_p3 = p3[0]
            abs_idx_p1 = len(closes) - lookback + offset_p1
            abs_idx_p3 = len(closes) - lookback + offset_p3
            if 0 <= abs_idx_p1 < len(rsi_s) and 0 <= abs_idx_p3 < len(rsi_s):
                rsi_p3 = rsi_s.iloc[abs_idx_p3]
                rsi_p1 = rsi_s.iloc[abs_idx_p1]
                rsi_div = rsi_p3 > rsi_p1  # 价格新低RSI新高
            else:
                rsi_div = False
            if rsi_div:
                score = max(score, 22)
            else:
                score = max(score, 12)

    return min(25, score)


def strategy_climax(code, closes, highs, lows, opens, volumes):
    """买入/卖出高潮检测 (AL Brooks Buying/Selling Climax)
    趋势加速 + 放量 + 长影线 → 力竭反转
    返回: (score: 0-20, climax_type: 'buying'|'selling'|'none')
    """
    if len(closes) < 10:
        return 0, 'none'

    # 最近5根K线实体大小
    bodies = []
    for i in range(-1, -6, -1):
        if abs(i) > len(closes):
            break
        body = abs(closes.iloc[i] - opens.iloc[i])
        bodies.append(body)

    if len(bodies) < 4:
        return 0, 'none'

    # 实体加速判定
    bodies_rev = list(reversed(bodies))
    accelerating = all(bodies_rev[i] > bodies_rev[i - 1] for i in range(1, len(bodies_rev)))

    # 最后一根K线分析
    cur_close = closes.iloc[-1]; cur_open = opens.iloc[-1]
    cur_high = highs.iloc[-1]; cur_low = lows.iloc[-1]
    final_range = max(cur_high - cur_low, 0.01)
    final_body = bodies[-1]
    final_body_ratio = final_body / final_range

    if cur_close >= cur_open:
        upper_wick = cur_high - cur_close
        lower_wick = cur_open - cur_low
        trend = 'buying'
    else:
        upper_wick = cur_high - cur_open
        lower_wick = cur_close - cur_low
        trend = 'selling'

    upper_ratio = upper_wick / final_range
    lower_ratio = lower_wick / final_range

    # 买入高潮: 长上影(买方力竭) + 实体适中
    buy_climax = (trend == 'buying' and upper_ratio > 0.30 and
                  0.35 < final_body_ratio < 0.85)
    # 卖出高潮: 长下影(卖方力竭) + 实体适中
    sell_climax = (trend == 'selling' and lower_ratio > 0.30 and
                   0.35 < final_body_ratio < 0.85)

    # 成交量爆发
    vol_ma20 = volumes.rolling(20).mean().iloc[-1]
    vol_ratio = volumes.iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1.0
    volume_surge = vol_ratio > 1.5

    # 收盘未在极值端
    close_pos = (cur_close - cur_low) / final_range
    close_rev = (trend == 'buying' and close_pos < 0.60) or \
                (trend == 'selling' and close_pos > 0.40)

    # 判定高潮类型和强度
    if buy_climax or sell_climax:
        climax_type = 'buying' if buy_climax else 'selling'
        if accelerating and volume_surge and close_rev:
            return 20, climax_type
        elif volume_surge:
            return 15, climax_type
        elif accelerating:
            return 12, climax_type
        else:
            return 8, climax_type
    elif volume_surge and accelerating and close_rev:
        return 5, 'none'

    return 0, 'none'


# ==================== 短线过热风险检测 ====================

def detect_overheat_risk(closes, highs, lows, opens, rsi_val):
    """检测短线过热风险
    连阳过多、连续上影线、短线涨幅过快、乖离过大
    返回: (risk_score, [reasons])
      风险分 0-30，正数表示要扣减的分数
    """
    if len(closes) < 20:
        return 0, []

    risk = 0
    reasons = []

    # --- 1. 连阳天数 ---
    yang_count = 0
    for i in range(len(closes) - 1, -1, -1):
        if closes.iloc[i] > opens.iloc[i]:
            yang_count += 1
        else:
            break
    if yang_count >= 7:
        risk += 12
        reasons.append(f'{yang_count}连阳过热(扣{12})')
    elif yang_count >= 5:
        risk += 6
        reasons.append(f'{yang_count}连阳偏热(扣{6})')

    # --- 2. 连续上影线 ---
    long_upper_count = 0
    for i in range(-1, -4, -1):
        if abs(i) > len(closes):
            break
        rng = highs.iloc[i] - lows.iloc[i]
        if rng > 0:
            upper = (highs.iloc[i] - max(closes.iloc[i], opens.iloc[i])) / rng
            if upper > 0.35:
                long_upper_count += 1
    if long_upper_count >= 2:
        risk += 8
        reasons.append(f'连续{int(long_upper_count)}天上影>35%(扣{8})')

    # --- 3. 短线涨幅过快 ---
    mom5 = (closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5] * 100
    if mom5 > 15:
        risk += 12
        reasons.append(f'5日涨{mom5:.0f}%过快(扣12)')
    elif mom5 > 10:
        risk += 8
        reasons.append(f'5日涨{mom5:.0f}%偏快(扣8)')
    elif mom5 > 8:
        risk += 4
        reasons.append(f'5日涨{mom5:.0f}%略快(扣4)')

    # --- 4. 乖离20日线 ---
    ma20 = closes.rolling(20).mean().iloc[-1]
    dev = (closes.iloc[-1] - ma20) / ma20 * 100
    if dev > 12:
        risk += 12
        reasons.append(f'偏离20日线{dev:.0f}%过大(扣12)')
    elif dev > 8:
        risk += 8
        reasons.append(f'偏离20日线{dev:.0f}%偏大(扣8)')
    elif dev > 5:
        risk += 3
        reasons.append(f'偏离20日线{dev:.0f}%(扣3)')

    # --- 5. RSI连续超买 ---
    if len(closes) >= 10:
        overbought_days = 0
        for i in range(-1, -6, -1):
            if abs(i) > len(closes):
                break
            window = closes.iloc[max(0, len(closes) + i - 14): len(closes) + i]
            if len(window) >= 14:
                delta = window.diff()
                g = delta.where(delta > 0, 0).mean()
                l = -delta.where(delta < 0, 0).mean()
                rs = g / l if l > 0 else 999
                r = 100 - 100 / (1 + rs)
                if r > 65:
                    overbought_days += 1
        if overbought_days >= 3:
            risk += 5
            reasons.append(f'近5日{overbought_days}天RSI>65(扣5)')

    return min(30, risk), reasons
def calculate_comprehensive_score(code):
    df = get_stock_daily_cached(code, 60)
    if df is None or len(df)<20: return None
    closes = df['close']; highs = df['high']; lows = df['low']
    rsi = compute_rsi(closes)
    adx_val, plus_di, minus_di = compute_adx(highs,lows,closes)
    # ==== AL Brooks 背景上下文分类 ====
    context_mult, context_label = classify_context_state(df, adx_val, plus_di, minus_di)
    macd_line,_,macd_hist = compute_macd(closes)
    k,d,j_val = compute_kdj(highs,lows,closes)
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
               'consecutive_yang': '连阳蓄势', 'cointegration': '协整套利',
               'three_push': '三推衰竭反转', 'climax': '趋势高潮'}

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
    s_coint = strategy_cointegration(code)
    # 新移植策略
    s_pullback = strategy_pullback_buy(code, closes, highs, lows)
    s_dual_thrust = strategy_dual_thrust(code, closes, highs, lows, opens)
    s_kk = strategy_king_keltner(code, closes, highs, lows)
    s_topk = strategy_topk_momentum(code, closes, highs, lows, volumes)
    s_weekday = strategy_weekday_effect(code, closes)
    s_two_yang = strategy_two_day_yang(code, closes, opens)
    # Brooks 策略
    s_three_push = strategy_three_push(code, closes, highs, lows, volumes)
    s_climax, s_climax_type = strategy_climax(code, closes, highs, lows, opens, volumes)

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
    adj_coint = get_adjusted_weight('cointegration'); score += int(s_coint*adj_coint)
    if s_coint>=15: reasons.append("协整套利(强)")
    elif s_coint>=10: reasons.append("协整套利")

    # 新移植策略
    score += int(s_pullback * get_adjusted_weight('pullback_buy', 1.0))
    if s_pullback>=15: reasons.append("回调买入")
    score += int(s_dual_thrust * 0.8)
    if s_dual_thrust>=15: reasons.append("DualThrust突破")
    score += int(s_kk * 0.8)
    if s_kk>=15: reasons.append("KingKeltner突破")
    score += int(s_topk * 0.6)
    if s_topk>=10: reasons.append("Top-K动量")
    score += int(s_weekday * 0.6)
    if s_weekday>=10: reasons.append("周几效应")
    score += int(s_two_yang * 0.8)
    if s_two_yang>=10: reasons.append("两连阳加速")

    # ==== AL Brooks 策略分数 ====
    score += int(s_three_push * 0.8)
    if s_three_push >= 20: reasons.append("三推衰竭反转(强)")
    elif s_three_push >= 15: reasons.append("三推衰竭反转")
    elif s_three_push >= 10: reasons.append("三推结构")
    # ==== 高潮信号处理 (方向修正：买入高潮扣分，卖出高潮加分) ====
    if s_climax > 0 and s_climax_type == 'buying':
        # 买入高潮 → 风险扣分（买盘力竭，次日不易高开）
        penalty = int(s_climax * 0.7)
        score -= penalty
        reasons.append(f"买入高潮(扣{penalty})")
    elif s_climax > 0 and s_climax_type == 'selling':
        # 卖出高潮 → 加分（卖盘力竭，潜在反转）
        bonus = int(s_climax * 0.4)
        score += bonus
        reasons.append(f"卖出高潮(+{bonus})")

    # ==== 短线过热风险检测 ====
    overheat_risk, heat_reasons = detect_overheat_risk(closes, highs, lows, opens, rsi)
    if overheat_risk > 0:
        score = max(0, score - overheat_risk)
        reasons.extend(heat_reasons)

    # ==== AL Brooks 信号K线质量 (SBQ) 加成 ====
    sbq_raw, sbq_type = compute_signal_bar_quality(df)
    io_type, io_adj = detect_inside_outside_bar(df)
    sbq_final = int(sbq_raw / 2) + io_adj
    score += sbq_final
    if sbq_raw >= 20: reasons.append(f"信号K线优质(SBQ={sbq_raw})")
    elif sbq_raw >= 15: reasons.append(f"信号K线可(SBQ={sbq_raw})")
    if io_type != 'none': reasons.append(f"{io_type}")

    if 30<=rsi<=50: score += 10; reasons.append("RSI健康" + (f"(+30%)" if mkt_adj.get('RSI',1.0)>1.1 else ""))
    # RSI超买阶梯扣分 (原仅扣5分，太轻)
    if rsi > 75:
        score -= 20
        reasons.append(f"RSI{rsi:.0f}严重超买(扣20)")
    elif rsi > 70:
        score -= 12
        reasons.append(f"RSI{rsi:.0f}超买(扣12)")
    elif rsi > 65:
        score -= 5
        reasons.append(f"RSI{rsi:.0f}偏高(扣5)")
    if adx_val and adx_val<20: score = int(score*0.92); reasons.append("ADX弱趋势(扣8%)")
    if vol_ratio<0.8: score = int(score*0.92); reasons.append("缩量(扣8%)")
    if 0.1<bp<0.4: score += 10; reasons.append("布林下轨" + (f"(+30%)" if mkt_adj.get('布林下轨',1.0)>1.1 else ""))
    if bp>0.85: score = int(score*0.9); reasons.append("布林上轨(扣10%)")
    # MACD为正（分析显示+16%预测力，最强单一信号）
    if macd_hist > 0: score += 15; reasons.append("MACD多头(+15)")
    # KDJ超卖是反向指标：超卖股次日冲高概率低11%
    if j_val < 20:
        score = int(score * 0.88)
        reasons.append("KDJ超卖(扣12%)")
    if j_val > 80:
        score = int(score * 0.85)
        reasons.append("KDJ超买(扣15%)")
    if abs(r_val)>0.7: score = int(score*0.85); reasons.append("高相关性跟风(扣15%)")

    score = _apply_dynamic_factors(code, df, score, reasons)

    # ==== AL Brooks 背景上下文调节器 ====
    if context_mult != 1.0:
        old_score = score
        score = max(0, int(score * context_mult))
        delta = score - old_score
        if delta > 0:
            reasons.append(f"趋势背景(×{context_mult:.2f}, +{delta})")
        elif delta < 0:
            reasons.append(f"震荡背景(×{context_mult:.2f}, {delta})")

    try:
        from kelly import calc_kelly_position
        k_res = calc_kelly_position(code, 0, min(round(score), 100))
        position_pct = k_res['kelly_pct']
    except Exception:
        position_pct = 30 if score>=80 else (20 if score>=65 else (10 if score>=50 else 5))

    # CtaSignal 引擎二次评分 (新旧混合)
    try:
        from signal_engine import SignalEngine, BullTrendSignal, RsiSignal, VolumeSignal, \
            MACrossSignal, ChanBreakSignal, BollChannelSignal, KDJOverboughtRisk, BollTopRisk, \
            WeakTrendRisk, LowVolumeRisk
        se = SignalEngine()
        se.add_signal(MACrossSignal(weight=1.0))
        se.add_signal(BullTrendSignal(weight=1.0))
        se.add_signal(BollChannelSignal(weight=0.8))
        se.add_signal(RsiSignal(weight=0.8))
        se.add_signal(VolumeSignal(weight=0.5))
        se.add_signal(ChanBreakSignal(weight=1.0))
        se.add_risk(KDJOverboughtRisk())
        se.add_risk(BollTopRisk())
        se.add_risk(WeakTrendRisk())
        se.add_risk(LowVolumeRisk())
        se_result = se.evaluate(df)
        # 新旧评分加权混合 (旧:新 = 6:4)
        score = int(score * 0.6 + se_result['signal'] * 0.4)
        reasons.append(f"新信号({se_result['signal']})")
    except Exception:
        pass

    # ==== Ruflo similarity bonus ====
    try:
        from mcp_client import safe_search
        query = f"Stock {code}: rsi={round(rsi,1)}, vr={round(vol_ratio,2)}, mom={round(mom5,2)}"
        similar = safe_search(query, top_k=3)
        if similar and len(similar) > 0:
            avg_sig = 0
            wins = 0
            for r in similar:
                meta = r.get('metadata', {})
                if meta.get('signal'):
                    avg_sig += meta['signal']
            avg_sig /= len(similar)
            win_rate = wins / len(similar) if len(similar) > 0 else 0
            if win_rate > 0.6:
                bonus = min(10, int(win_rate * 10))
                score = min(100, score + bonus)
                reasons.append(f"Ruflo相似({wins}/{len(similar)}, +{bonus})")
            elif win_rate < 0.3 and len(similar) >= 2:
                penalty = min(8, int((1 - win_rate) * 8))
                score = max(0, score - penalty)
                reasons.append(f"Ruflo相似({wins}/{len(similar)}, -{penalty})")
    except ImportError:
        pass
    except Exception:
        pass

    return {
        'code':code, 'name':'', 'price':0, 'change_pct':0,
        'signal': min(round(score),100),
        'rsi':round(rsi,2), 'adx':round(adx_val,2) if adx_val else 0,
        'macd_hist':round(macd_hist,4), 'macd_positive':int(macd_hist > 0),
        'kdj_k':round(k,2), 'kdj_j':round(j_val,2),
        'resid_z':round(resid_z,2), 'bb_position':round(bp,2),
        'momentum_5d':round(mom5,2), 'volume_ratio':round(vol_ratio,2),
        'priority_reason': ' + '.join(reasons[:4]) if reasons else '多因子共振',
        'position_advice': f"建议仓位{position_pct}%",
        'strategy_scores':{'均线金叉':s_ma,'缠论':s_chan,'波浪理论':s_wave,'多头趋势':s_bull,'热点题材':s_topic,'事件驱动':s_event,'成长质量':s_growth,'预期重估':s_reval,'蛟龙出海':s_dragon,'上山爬坡':s_climb,'布林突破':s_boll,'量价配合':s_vol_price,'三金叉共振':s_triple,'超跌反转':s_oversold,'动量突破':s_momentum,'低波突破':s_lowvol,'连阳蓄势':s_yang,'协整套利':s_coint,'回调买入':s_pullback,'DualThrust':s_dual_thrust,'KingKeltner':s_kk,'TopK':s_topk,'周几效应':s_weekday,'两连阳加速':s_two_yang,'信号K线质量':sbq_raw,'三推反转':s_three_push,'趋势高潮':s_climax},
        'basic_info': info, 'sectors': sectors,
        'brooks': {'sbq': sbq_raw, 'sbq_type': sbq_type, 'inside_outside': io_type, 'context': context_label, 'three_push': s_three_push, 'climax': s_climax, 'climax_type': s_climax_type}
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
                        'consecutive_yang', 'cointegration']:
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
            score_map['cointegration'] = strategy_cointegration(code)
            score_map['three_push'] = strategy_three_push(code, closes, highs, lows, volumes)
            climax_score, _ = strategy_climax(code, closes, highs, lows,
                df['open'] if 'open' in df.columns else closes, volumes)
            score_map['climax'] = climax_score
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
            'consecutive_yang': '连阳蓄势', 'cointegration': '协整套利',
            'three_push': '三推衰竭反转', 'climax': '趋势高潮'
        }
        for sname, fname in strategy_to_factor.items():
            if sname in factor_stats and fname in FACTOR_REGISTRY:
                FACTOR_REGISTRY[fname]['weight'] = factor_stats[sname]['weight']
                FACTOR_REGISTRY[fname]['win_rate'] = factor_stats[sname].get('win_rate', 0)

        save_json(factor_stats, FACTOR_STATS_FILE)
        save_registry()
    except Exception:
        pass
