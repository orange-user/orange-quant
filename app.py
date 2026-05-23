import datetime
import random
import json
import os
import threading
import numpy as np
import pandas as pd
from flask import Flask, render_template, jsonify, request
import akshare as ak
from scipy import stats
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

app = Flask(__name__)

DATA_DIR = 'data'
os.makedirs(DATA_DIR, exist_ok=True)
POSITION_FILE = os.path.join(DATA_DIR, 'positions.json')
TRADE_FILE = os.path.join(DATA_DIR, 'trades.json')
DIARY_FILE = os.path.join(DATA_DIR, 'diary.json')

daily_cache = {}
cache_lock = threading.Lock()

def get_weekday():
    return datetime.datetime.now().weekday()

def load_json(path, default=None):
    if default is None: default = [] if 'positions' in path or 'trades' in path or 'diary' in path else {}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default

def save_json(data, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ==================== 数据获取 ====================
def get_stock_daily_cached(code, days=60):
    today = datetime.date.today()
    with cache_lock:
        if code in daily_cache and daily_cache[code]['date'] == today.isoformat():
            df = daily_cache[code]['df']
            if len(df) >= days:
                return df.tail(days).copy()
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if len(df) < days:
            return None
        df = df.tail(days).copy()
        df['returns'] = df['收盘'].pct_change()
        df['volume_ratio'] = df['成交量'] / df['成交量'].rolling(20).mean()
        df['ma5'] = df['收盘'].rolling(5).mean()
        df['ma20'] = df['收盘'].rolling(20).mean()
        result = df[['日期', '开盘', '收盘', '最高', '最低', '成交量', 'returns', 'volume_ratio', 'ma5', 'ma20']].dropna()
        with cache_lock:
            daily_cache[code] = {'date': today.isoformat(), 'df': result.copy()}
        return result
    except:
        return None

def get_index_daily(code="sh000300", days=60):
    try:
        df = ak.stock_zh_index_daily(symbol=code)
        df = df.tail(days).copy()
        df['returns'] = df['close'].pct_change()
        return df[['date', 'close', 'returns']].dropna()
    except:
        return None

def get_stock_info(code):
    try:
        df = ak.stock_individual_info_em(symbol=code)
        info = {}
        for _, row in df.iterrows():
            info[row['item']] = row['value']
        return info
    except:
        return {}

def get_stock_sector(code):
    try:
        df = ak.stock_board_concept_cons_em(symbol=code)
        if df is not None and len(df) > 0:
            return df['板块名称'].head(5).tolist()
        return []
    except:
        return []

def get_30min_data(code, days=5):
    try:
        df = ak.stock_zh_a_hist_min_em(symbol=code, period='30', adjust='qfq')
        if df is None or len(df) < 20:
            return None
        df = df.tail(days * 8)
        df['returns'] = df['收盘'].pct_change()
        return df[['时间', '开盘', '收盘', '最高', '最低', '成交量', '成交额', 'returns']].dropna()
    except:
        return None

def get_auction_data(code):
    try:
        df = ak.stock_zh_a_spot_em()
        row = df[df['代码'] == code]
        if len(row) == 0:
            return None
        open_price = float(row['今开'].values[0]) if '今开' in row.columns else None
        pre_close = float(row['昨收'].values[0]) if '昨收' in row.columns else None
        volume = float(row['成交量'].values[0]) if '成交量' in row.columns else 0
        amount = float(row['成交额'].values[0]) if '成交额' in row.columns else 0
        if open_price and pre_close:
            return {'open': open_price, 'pre_close': pre_close, 'volume': volume, 'amount': amount}
        return None
    except:
        return None

def get_sector_change(code):
    try:
        info = get_stock_info(code)
        industry = info.get('行业', '')
        if not industry:
            return 0
        df = ak.stock_board_industry_name_em()
        row = df[df['板块名称'] == industry]
        if len(row) > 0:
            return float(row['涨跌幅'].values[0])
        return 0
    except:
        return 0

def get_market_change():
    try:
        df = ak.stock_zh_index_daily(symbol="sh000001")
        if len(df) >= 1:
            today = df.tail(1)
            close = float(today['close'].values[0])
            pct = float(today['pct_chg'].values[0]) if 'pct_chg' in df.columns else 0
            return close, pct
        return 0, 0
    except:
        return 0, 0

# ==================== 因子计算 ====================
def compute_stat_arb_signal(stock_returns, index_returns):
    if len(stock_returns) < 30:
        return None
    min_len = min(len(stock_returns), len(index_returns))
    y = stock_returns.values[-min_len:]
    x = index_returns.values[-min_len:]
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)
    resid = y - (intercept + slope * x)
    resid_mean = np.mean(resid)
    resid_std = np.std(resid)
    if resid_std == 0:
        return None
    resid_z = (resid[-1] - resid_mean) / resid_std
    return resid_z, r_value

def compute_adx(high, low, close, period=14):
    if len(close) < period + 1:
        return None, None, None
    tr = pd.DataFrame({
        'h-l': high - low,
        'h-pc': abs(high - close.shift(1)),
        'l-pc': abs(low - close.shift(1))
    }).max(axis=1)
    atr = tr.rolling(period).mean()
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > 0) & (up > down), 0)
    minus_dm = down.where((down > 0) & (down > up), 0)
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(period).mean()
    return adx.iloc[-1], plus_di.iloc[-1], minus_di.iloc[-1]

def compute_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast).mean()
    ema_slow = close.ewm(span=slow).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal).mean()
    hist = macd - signal_line
    return macd.iloc[-1], signal_line.iloc[-1], hist.iloc[-1]

def compute_kdj(high, low, close, n=9):
    lowest = low.rolling(n).min()
    highest = high.rolling(n).max()
    rsv = (close - lowest) / (highest - lowest) * 100
    k = rsv.ewm(com=2).mean()
    d = k.ewm(com=2).mean()
    j = 3 * k - 2 * d
    return k.iloc[-1], d.iloc[-1], j.iloc[-1]

def compute_tail_momentum(code):
    df = get_30min_data(code, days=5)
    if df is None or len(df) < 10:
        return 0, False, 0
    df['时间_idx'] = pd.to_datetime(df['时间'])
    tail_mask = (df['时间_idx'].dt.hour == 14) & (df['时间_idx'].dt.minute >= 30) | (df['时间_idx'].dt.hour == 15)
    tail_df = df[tail_mask].copy()
    if len(tail_df) < 3:
        return 0, False, 0
    tail_df['amount_change'] = tail_df['成交额'].diff()
    recent_inflow = tail_df['amount_change'].tail(2).sum()
    has_inflow = recent_inflow > 0
    tail_df['price_change'] = tail_df['收盘'].pct_change()
    tail_df['big_order_proxy'] = tail_df['price_change'] * tail_df['成交量']
    big_order_strength = tail_df['big_order_proxy'].tail(4).sum()
    big_order_score = min(20, max(0, big_order_strength * 10)) if big_order_strength > 0 else 0
    recent_10 = df.tail(10)
    if len(recent_10) < 10:
        return 0, has_inflow, 0
    price_displacement = (recent_10['收盘'].iloc[-1] - recent_10['收盘'].iloc[-10]) / recent_10['收盘'].iloc[-10]
    recent_10['tr'] = np.maximum(
        recent_10['最高'] - recent_10['最低'],
        np.maximum(abs(recent_10['最高'] - recent_10['收盘'].shift(1)), abs(recent_10['最低'] - recent_10['收盘'].shift(1)))
    )
    avg_volatility = recent_10['tr'].mean()
    if avg_volatility == 0:
        return 0, has_inflow, 0
    raw_momentum_intensity = price_displacement / avg_volatility
    if 'momentum_ema' not in compute_tail_momentum.__dict__:
        compute_tail_momentum.momentum_ema = {}
    if code not in compute_tail_momentum.momentum_ema:
        compute_tail_momentum.momentum_ema[code] = 0
    compute_tail_momentum.momentum_ema[code] = 0.3 * raw_momentum_intensity + 0.7 * compute_tail_momentum.momentum_ema[code]
    smoothed_intensity = compute_tail_momentum.momentum_ema[code]
    vol_weight = recent_10['成交量'].tail(5).mean() / recent_10['成交量'].mean() if recent_10['成交量'].mean() > 0 else 1
    adjusted_momentum = smoothed_intensity * vol_weight
    dynamic_threshold = 0.5 * (1 - min(avg_volatility * 10, 0.8))
    momentum_score = 0
    if adjusted_momentum > dynamic_threshold and has_inflow:
        momentum_score = min(25, int(adjusted_momentum * 25 + 10))
    return momentum_score, has_inflow, big_order_score

def compute_volume_shrink(code):
    df = get_stock_daily_cached(code, 20)
    if df is None or len(df) < 10:
        return 0
    closes = df['收盘'].values
    highs = df['最高'].values
    volumes = df['成交量'].values
    prev_high = highs[-2]
    recent_4_highs = highs[-6:-1]
    is_prev_high_peak = prev_high >= max(recent_4_highs)
    avg_vol_3d = np.mean(volumes[-4:-1])
    today_vol = volumes[-1]
    is_shrink = today_vol < avg_vol_3d * 0.9
    today_change = (closes[-1] - closes[-2]) / closes[-2] * 100
    is_small_pullback = -3 < today_change < 0
    score = 0
    if is_prev_high_peak:
        score += 5
    if is_shrink:
        score += 10
    if is_small_pullback:
        score += 10
    return min(25, score)

def get_sell_advice(rsi, momentum_5d, adx, plus_di, minus_di, macd_hist, k_val, d_val):
    advice = []
    if rsi > 65:
        advice.append("RSI偏高，高开>2%立即止盈")
    elif rsi > 50:
        advice.append("开盘冲高可分批卖出")
    else:
        advice.append("观察开盘量能，放量持有至10:00")
    if adx > 30 and plus_di > minus_di:
        advice.append("趋势强劲，可持有至午盘")
    elif adx < 20:
        advice.append("趋势不明，有盈利就落袋")
    if momentum_5d > 8:
        advice.append("短线涨幅已大，竞价减仓")
    if macd_hist > 0 and k_val > 80:
        advice.append("MACD金叉+KDJ超买，冲高即卖")
    sell_time = "次日9:25-9:35竞价+开盘区间卖出" if rsi > 55 else "次日10:00前择机卖出"
    return {'sell_time': sell_time, 'details': advice[:3]}

def calculate_tail_signal(code):
    stock_df = get_stock_daily_cached(code, 60)
    index_df = get_index_daily("sh000300", 60)
    if stock_df is None or index_df is None:
        return None
    stock_ret = stock_df.set_index('日期')['returns']
    index_ret = index_df.set_index('date')['returns']
    common_idx = stock_ret.index.intersection(index_ret.index)
    if len(common_idx) < 30:
        return None
    s_ret = stock_ret[common_idx].dropna()
    i_ret = index_ret[common_idx].dropna()
    if len(s_ret) < 30:
        return None
    resid_z, r_val = compute_stat_arb_signal(s_ret, i_ret)
    if resid_z is None:
        return None
    closes = stock_df.set_index('日期')['收盘'][common_idx].dropna()
    if len(closes) < 20:
        return None
    rolling_mean = closes.rolling(20).mean()
    rolling_std = closes.rolling(20).std()
    bb_lower = rolling_mean - 2 * rolling_std
    bb_upper = rolling_mean + 2 * rolling_std
    if pd.isna(bb_lower.iloc[-1]):
        return None
    bb_position = (closes.iloc[-1] - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1])
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    rs = gain.rolling(14).mean() / loss.rolling(14).mean()
    rsi = 100 - (100 / (1 + rs.iloc[-1])) if not pd.isna(rs.iloc[-1]) else 50
    momentum_5d = (closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5] * 100
    momentum_20d = (closes.iloc[-1] - closes.iloc[-20]) / closes.iloc[-20] * 100
    volume_ratio = stock_df['volume_ratio'].iloc[-1] if 'volume_ratio' in stock_df.columns else 1.0
    recent_amplitude = (stock_df['最高'].iloc[-1] - stock_df['最低'].iloc[-1]) / stock_df['收盘'].iloc[-1] * 100
    highs = stock_df.set_index('日期')['最高'][common_idx].dropna()
    lows = stock_df.set_index('日期')['最低'][common_idx].dropna()
    adx_val, plus_di, minus_di = compute_adx(highs, lows, closes)
    if adx_val is None:
        adx_val, plus_di, minus_di = 0, 0, 0
    macd_val, macd_signal, macd_hist = compute_macd(closes)
    k_val, d_val, j_val = compute_kdj(highs, lows, closes)
    ma5 = stock_df['ma5'].iloc[-1] if 'ma5' in stock_df.columns else closes.iloc[-1]
    ma20 = stock_df['ma20'].iloc[-1] if 'ma20' in stock_df.columns else closes.iloc[-1]
    mom_score, has_inflow, big_score = compute_tail_momentum(code)
    shrink_score = compute_volume_shrink(code)
    if resid_z >= -1.5:
        return None
    if not has_inflow:
        return None
    score = 0
    reasons = []
    if resid_z < -2.5:
        score += 25
        reasons.append("深度超卖")
    elif resid_z < -1.5:
        score += 15
        reasons.append("统计超卖")
    if 0.1 < bb_position < 0.4:
        score += 15
        reasons.append("布林下轨支撑")
    if 30 <= rsi <= 50:
        score += 10
        reasons.append("RSI健康区间")
    elif 50 < rsi <= 60:
        score += 5
    if 1 < momentum_5d < 5:
        score += 10
        reasons.append("短期温和上涨")
    elif -2 < momentum_5d <= 1:
        score += 5
    if ma5 > ma20:
        score += 8
        reasons.append("均线多头排列")
    if macd_hist > 0 and k_val < 80:
        score += 8
        reasons.append("MACD金叉KDJ未超买")
    elif macd_hist > 0:
        score += 4
    if 1.2 < volume_ratio < 3.0:
        score += 8
        reasons.append("放量温和活跃")
    if recent_amplitude < 5:
        score += 5
    elif recent_amplitude > 8:
        score -= 10
    if adx_val > 25 and plus_di > minus_di:
        score += 10
        reasons.append("ADX趋势确认")
    if momentum_20d > -5 and momentum_5d > 0:
        score += 5
    score += mom_score + big_score + shrink_score
    if abs(r_val) < 0.4:
        score *= 0.7
    priority_reason = " + ".join(reasons[:4]) if reasons else "多因子共振信号"
    sell_advice = get_sell_advice(rsi, momentum_5d, adx_val, plus_di, minus_di, macd_hist, k_val, d_val)
    return {
        'code': code,
        'resid_z': round(resid_z, 2), 'bb_position': round(bb_position, 2),
        'rsi': round(rsi, 2), 'momentum_5d': round(momentum_5d, 2),
        'volume_ratio': round(volume_ratio, 2), 'amplitude': round(recent_amplitude, 2),
        'adx': round(adx_val, 1), 'correlation': round(r_val, 2),
        'macd_hist': round(macd_hist, 4), 'kdj_k': round(k_val, 1), 'kdj_d': round(d_val, 1),
        'ma5': round(ma5, 2), 'ma20': round(ma20, 2),
        'tail_momentum': mom_score, 'big_order': big_score, 'shrink_score': shrink_score,
        'sell_advice': sell_advice,
        'priority_reason': priority_reason,
        'signal': min(round(score), 100)
    }

# ==================== 持仓管理 ====================
def load_positions():
    return load_json(POSITION_FILE, [])

def save_positions(data):
    save_json(data, POSITION_FILE)

def load_trades():
    return load_json(TRADE_FILE, [])

def save_trades(data):
    save_json(data, TRADE_FILE)

def calc_stats():
    trades = load_trades()
    positions = load_positions()
    total_profit = sum(t.get('profit', 0) for t in trades)
    win_trades = [t for t in trades if t.get('profit', 0) > 0]
    win_rate = len(win_trades) / len(trades) * 100 if trades else 0
    floating = 0
    for p in positions:
        try:
            df = ak.stock_zh_a_spot_em()
            row = df[df['代码'] == p['code']]
            if len(row) > 0:
                curr_price = float(row['最新价'].values[0])
                p['current_price'] = curr_price
                p['float_profit'] = round((curr_price - p['buy_price']) / p['buy_price'] * 100, 2)
                floating += (curr_price - p['buy_price']) * p['shares']
        except:
            pass
    return {
        'total_trades': len(trades),
        'win_rate': round(win_rate, 1),
        'total_profit': round(total_profit, 2),
        'floating_pnl': round(floating, 2),
        'positions': positions
    }

# ==================== 新闻板块映射（完整版） ====================
def analyze_news_sectors(title, content):
    text = title + content
    sector_map = {
        '电力': {'keyword': ['电力', '电网', '能源', '发电', '变电站', '特高压', '输电', '算电协同', '新能源'], 'sector': '电力板块'},
        '半导体': {'keyword': ['芯片', '半导体', '集成电路', '光刻', '晶圆', '算力', '先进封装', '存储'], 'sector': '半导体板块'},
        'AI': {'keyword': ['人工智能', 'AI', '大模型', 'GPT', '深度学习', '机器学习', '智能', '机器人', '具身智能'], 'sector': 'AI人工智能板块'},
        '新能源车': {'keyword': ['新能源车', '电动车', '锂电池', '动力电池', '充电桩', '汽车', '固态电池'], 'sector': '新能源汽车板块'},
        '银行': {'keyword': ['降准', '降息', '银行', '信贷', '贷款', '利率', 'LPR', '存款'], 'sector': '银行板块'},
        '房地产': {'keyword': ['房地产', '房价', '楼市', '购房', '房贷', '住房', '城中村'], 'sector': '房地产板块'},
        '医药': {'keyword': ['医药', '医疗', '药品', '疫苗', '生物', '创新药', '医疗器械', '中药'], 'sector': '医药板块'},
        '消费': {'keyword': ['消费', '零售', '电商', '家电', '白酒', '食品', '餐饮', '旅游', '免税'], 'sector': '消费板块'},
        '军工': {'keyword': ['军工', '国防', '武器', '军事', '航天', '航空发动机', '导弹'], 'sector': '军工板块'},
        '光伏': {'keyword': ['光伏', '太阳能', '硅料', '组件', '逆变器', '储能'], 'sector': '光伏板块'},
        '5G': {'keyword': ['5G', '6G', '通信', '基站', '光纤', '卫星互联网'], 'sector': '5G通信板块'},
        '农业': {'keyword': ['农业', '粮食', '种子', '转基因', '化肥', '养殖'], 'sector': '农业板块'},
    }
    results = []
    for key, val in sector_map.items():
        for kw in val['keyword']:
            if kw in text:
                results.append({'sector': val['sector'], 'type': '利好'})
                break
    bearish_words = ['下跌', '暴跌', '崩盘', '危机', '风险', '制裁', '贸易战', '关税', '加息', '收紧', '监管', '处罚']
    if any(w in text for w in bearish_words):
        for r in results:
            r['type'] = '利空'
    return results[:3]

# ==================== 易命二十八术（完整28条） ====================
YIMING_28 = [
    {'id':1,'text':'洒扫庭除，使身不近秽；肃洁仪容，使秽不附身。勿卧于污秽熏天、杂然无序之所，勿寝于阴暗潮湿之地。','translation':'保持身体和环境清洁，远离污秽和阴暗潮湿之处。','summary':'整洁养气'},
    {'id':2,'text':'应时而兴，应时而食，应时而作，应时而息。四时有序，心神乃一。','translation':'按季节规律起床、饮食、劳作、休息，四季有序，心神才能专注。','summary':'顺应天时'},
    {'id':3,'text':'言讷而实，语善而真。不泄恶语，不传妄言，不涉谤讥。君子之运，发于唇齿。','translation':'说话谨慎真实，言辞善良，不说恶言、不传谣言、不诽谤。好运从口中来。','summary':'慎言积福'},
    {'id':4,'text':'以见利之明处世，财可求也。以侵害之心得财，悖逆天道，必遭反报：非数倍偿赎，即气运消减，身心俱伤。','translation':'用正当的眼光谋取财富可以，但以侵害他人之心得财，违背天道，必遭报应。','summary':'取财有道'},
    {'id':5,'text':'旧过过，未未到。事有先后，逐一面之，戒之在贪。','translation':'过去已过去，未来尚未到。事情有先后顺序，逐一面对，切忌贪多。','summary':'专注当下'},
    {'id':6,'text':'天时至，气运生，体察而顺应之。若能与道偕行，则如顺水行舟，无往不利。','translation':'时机到了，好运自然生发，要敏锐察觉并顺势而为，如同顺水行舟。','summary':'顺势而为'},
    {'id':7,'text':'今人多趋捷径，然未察其径塞途拥，而康庄之衢，阒其无人。','translation':'人人都想走捷径，却不知捷径往往拥挤堵塞，而宽阔大道上反而没人。','summary':'大道至简'},
    {'id':8,'text':'业之道，旧业勿轻弃。虽利薄，然根基之所系，存身之本也。若新业既成，根基稳固，方可择之而从。若二者得兼，善之善者也。','translation':'不要轻易放弃旧业，即使利润微薄，那是根基所在。新业稳固后再择之。','summary':'守本创新'},
    {'id':9,'text':'初遇生人，若生厌憎，心神不安，速避之。见使君怡然者，可近而交，然须慎察其行。必试其信义，此乃立世之本。若察其信劣，急避如避刃矢。','translation':'遇到让你不舒服的陌生人立刻远离，让你愉悦的可以接近，但需谨慎观察其品行。','summary':'择人而交'},
    {'id':10,'text':'父母者，天授贵人也；困厄中施以援手者，贵人也；指迷津于惘途者，贵人也；甘苦与共之友，贵人也；生死相托之夫妻，互为贵人也。','translation':'父母是天赐贵人，困境中帮助你的、迷茫中指路的、共甘共苦的朋友、生死相托的伴侣都是贵人。','summary':'珍惜贵人'},
    {'id':11,'text':'吾观处世之道，事未成勿泄于未预者，利既获勿宣于不知者。倘炫其功，恐招非议而损其益。','translation':'事没做成不要告诉无关的人，获利了不要宣扬。炫耀会招来非议，损害收益。','summary':'藏锋守拙'},
    {'id':12,'text':'凡所当为之事，必竭诚以赴，务尽其能。若存苟且之心，或怀怠惰之意，敷衍塞责，则不如止而不为。盖草率而成者，恐招无妄之灾，遗患无穷，终损己身之气运。','translation':'该做的事必须全力以赴，如果敷衍了事，不如不做，草率可能招来无妄之灾。','summary':'全力以赴'},
    {'id':13,'text':'识人者，当涤尽浮光，弃置衔冕，略其形骸，屏绝人议。惟观其言行之微，察其举止之细，则彼若素缣一卷，自将仁善鄙诈，贞邪曲直，书而示汝矣。','translation':'看一个人要去掉光环、头衔、外表和他人评价，只观察他的言行细节，真相自现。','summary':'透过表象'},
    {'id':14,'text':'御人当以疑始，必待其事毕。绳可束其身，岂能羁其志？人为灵长，旦暮异焉，唯以疑目察之，乃见其纤隐之变。','translation':'管理他人要先存疑心，直到事情结束。人是善变的，保持警觉才能察觉细微变化。','summary':'审慎御人'},
    {'id':15,'text':'判事之成否，其下者取利，其次者取鉴，其至者观乎气运之蓄。','translation':'判断事情成败：最低看利益，中等的看经验教训，最高的是看气运的积累。','summary':'格局为重'},
    {'id':16,'text':'交游之众，当分三等：其一唯利是图，毋涉情义；其二唯情是守，毋涉利欲；此二者不可逾，逾则必遭其咎。其三情利皆可谋。得此辈愈众，则气运愈昌。','translation':'朋友分三种：只谈利益的别谈感情，只谈感情的别谈利益，第三种是利益和感情都可以兼顾的。','summary':'交友分类'},
    {'id':17,'text':'方时运至际，万象皆佐。所当为者，惟体察而顺应之，乘势而起。然骄矜造作，挥霍无度，实悖逆福运。慎之。','translation':'好运来临时万事都会配合，只需顺势而为。但骄傲做作、挥霍无度会背离福运。','summary':'乘势忌骄'},
    {'id':18,'text':'共事欲成，首在谋宜而见远，次在尽其才，至要者忘私。三者备，则事可成其八九。余者，顺天时而已。','translation':'合作成功：首先谋划得当目光长远，其次人尽其才，最重要的是忘掉私心。','summary':'忘私成事'},
    {'id':19,'text':'观一人，细察之，见其品性端良，乃欲与之交。可试询其隐衷，若其诚言不讳，则证其心已视尔为友矣。','translation':'观察一个人品性端良后想交往，可以试着问他的心事，如果他坦诚相告，说明已把你当朋友。','summary':'以诚试友'},
    {'id':20,'text':'逢困厄而能自疏者，有福之人也；临险阻犹可坦然而笑者，具大运之相也。','translation':'遇到困境能自我疏导的人是有福之人，面临险阻还能坦然微笑的人有大运气。','summary':'困境从容'},
    {'id':21,'text':'众皆悦之，险必伏焉；众皆趋之，祸必随焉。举世所好，多为伐命之斧；人弃我取，常是纳福之门。','translation':'大家都喜欢的往往暗藏危险，大家都追逐的往往伴随灾祸。被人抛弃的可能正是机会。','summary':'逆向思维'},
    {'id':22,'text':'夫妇之道，首以信为万事之基，次则消弭龃龉，复可截长续短，尤贵同心以御外。','translation':'夫妻相处首重信任，其次消除矛盾、互相弥补，最重要的是同心抵御外患。','summary':'夫妻同心'},
    {'id':23,'text':'身者，命之宅也；福者，运之粮也。不惜其身，则宅坏；不省其福，则粮竭。纵欲、熬夜、暴食、暴怒，皆为自毁气运。','translation':'身体是命运的居所，福气是运气的粮食。不惜身体则居所破败，不省福气则粮食枯竭。','summary':'惜身惜福'},
    {'id':24,'text':'欲得言谈之真味，必以平等相对。至亲犹然。倘彼执守尊卑之见，则所对无非虚文俗套耳。','translation':'想要真诚交流必须以平等相待，至亲亦然。如果抱着尊卑观念，得到的只是虚伪客套。','summary':'平等交流'},
    {'id':25,'text':'出资市金，逐盐铁之利者，如博戏，肇端当有尽付一掷之志。所谓必成者，诈也。至若合营之事，尤忌心疑而不决，亦忌心贪而不止。','translation':'投资赚钱如同博弈，开始就要有全部亏掉的心理准备。承诺必赚的都是骗人的。','summary':'投资如博'},
    {'id':26,'text':'不欺暗室，不欺本心。人不见而神见，人不知而自知。暗室亏心，神目如电；一念纯正，福自相随。','translation':'在没人看见的地方也不欺骗自己。人看不见但天看见，别人不知但自己知道。','summary':'不欺本心'},
    {'id':27,'text':'勿强求险巇之事以耽危殆之娱，如此则大伤气运。向者无恙，惟气运未尽耳。','translation':'不要追求危险刺激的事情来获得快乐，这会大伤气运。之前没事只是气运还没用尽。','summary':'远离险乐'},
    {'id':28,'text':'勿自贻伊戚，凡桎梏皆厄气运。外锢虽不可御，内心毋复筑樊牢。心若自在，运自通达。','translation':'不要自寻烦恼，束缚都会困住气运。外部约束无法避免，但内心不要再给自己筑牢笼。心自由了，运气自然通达。','summary':'心自由运通'},
]

# ==================== 路由 ====================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze():
    wd = get_weekday()
    if wd == 5:
        return jsonify({'status': 'weekend_rest', 'message': '🛌 周末休市，好好休息！交易日14:30执行尾盘扫描。', 'stocks': []})
    if wd == 6:
        return jsonify({'status': 'sunday_review', 'message': '📈 周日复盘时间，回顾本周策略表现。'})
    try:
        pool = ak.stock_zh_a_spot_em()
        pool = pool[~pool['名称'].str.contains('ST|退')]
        pool = pool[pool['总市值'] > 50e8]
        pool = pool[pool['最新价'] > 3]
        codes = pool['代码'].tolist()
        total = len(codes)
        candidates = []
        with ThreadPoolExecutor(max_workers=12) as ex:
            futures = {ex.submit(calculate_tail_signal, c): c for c in codes}
            for fut in as_completed(futures):
                res = fut.result()
                if res and res['signal'] > 45:
                    row = pool[pool['代码'] == res['code']]
                    if len(row) == 0:
                        continue
                    row = row.iloc[0]
                    info = get_stock_info(res['code'])
                    sectors = get_stock_sector(res['code'])
                    candidates.append({
                        'code': res['code'], 'name': row['名称'], 'price': row['最新价'],
                        'change_pct': row['涨跌幅'], 'turnover': row['换手率'],
                        'signal': res['signal'],
                        'resid_z': res['resid_z'], 'bb_position': res['bb_position'],
                        'rsi': res['rsi'], 'momentum_5d': res['momentum_5d'],
                        'volume_ratio': res['volume_ratio'], 'amplitude': res['amplitude'],
                        'adx': res['adx'],
                        'macd_hist': res['macd_hist'], 'kdj_k': res['kdj_k'], 'kdj_d': res['kdj_d'],
                        'ma5': res['ma5'], 'ma20': res['ma20'],
                        'tail_momentum': res['tail_momentum'], 'big_order': res['big_order'],
                        'shrink_score': res['shrink_score'],
                        'priority_reason': res['priority_reason'],
                        'sell_advice': res['sell_advice'],
                        'basic_info': {
                            '总市值': info.get('总市值', 'N/A'),
                            '市盈率': info.get('市盈率-动态', row.get('市盈率-动态', 'N/A')),
                            '市净率': info.get('市净率', 'N/A'),
                            '行业': info.get('行业', 'N/A')
                        },
                        'sectors': sectors
                    })
        candidates.sort(key=lambda x: x['signal'], reverse=True)
        return jsonify({
            'status': 'ok',
            'message': f'多线程扫描 {total} 只全A股，筛选出 {len(candidates)} 个信号',
            'stocks': candidates[:15],
            'total': len(candidates)
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/sell_check', methods=['POST'])
def sell_check():
    data = request.get_json()
    code = data.get('code', '').strip()
    buy_price = float(data.get('buy_price', 0))
    if not code:
        return jsonify({'error': '请提供股票代码'}), 400
    try:
        spot = ak.stock_zh_a_spot_em()
        row = spot[spot['代码'] == code]
        if len(row) == 0:
            return jsonify({'error': '未找到该股票'}), 404
        current_price = float(row['最新价'].values[0])
        change_pct = float(row['涨跌幅'].values[0]) if '涨跌幅' in row.columns else 0
        turnover = float(row['换手率'].values[0]) if '换手率' in row.columns else 0
        auction = get_auction_data(code)
        sector_change = get_sector_change(code)
        _, market_change = get_market_change()
        df = get_stock_daily_cached(code, 30)
        if df is not None:
            closes = df['收盘']
            delta = closes.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            rsi = 100 - (100 / (1 + gain.rolling(14).mean() / loss.rolling(14).mean())) if loss.rolling(14).mean().iloc[-1] != 0 else 50
            rsi = float(rsi.iloc[-1]) if hasattr(rsi, 'iloc') else 50
        else:
            rsi = 50
        profit_pct = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
        should_sell = False
        reasons = []
        if profit_pct > 3:
            should_sell = True
            reasons.append(f"已盈利 {profit_pct:.1f}%，建议止盈")
        elif profit_pct > 1.5 and rsi > 70:
            should_sell = True
            reasons.append(f"盈利 {profit_pct:.1f}% 且 RSI={rsi:.0f} 偏高，建议减仓")
        elif profit_pct < -2:
            should_sell = True
            reasons.append(f"亏损 {profit_pct:.1f}%，触发止损线，建议卖出")
        elif rsi > 75:
            should_sell = True
            reasons.append(f"RSI={rsi:.0f} 严重超买，建议卖出")
        elif change_pct > 5 and turnover > 10:
            should_sell = True
            reasons.append(f"今日涨幅 {change_pct}% 换手率 {turnover}%，冲高回落风险大")
        if auction and auction['open'] > auction['pre_close'] * 1.03:
            should_sell = True
            reasons.append("竞价高开超过3%，落袋为安")
        if sector_change < -1:
            reasons.append(f"所属板块今日下跌 {sector_change}%，板块拖累个股")
        if market_change < -0.5:
            reasons.append(f"大盘今日下跌 {market_change}%，整体环境偏弱")
        if not should_sell:
            reasons.append(f"当前盈利 {profit_pct:.1f}%，RSI={rsi:.0f}，可继续持有，关注10:00走势")
        return jsonify({
            'code': code,
            'buy_price': buy_price,
            'current_price': current_price,
            'profit_pct': round(profit_pct, 2),
            'change_pct': change_pct,
            'rsi': round(rsi, 1),
            'should_sell': should_sell,
            'reasons': reasons,
            'advice': '建议立即卖出' if should_sell else '建议继续持有'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/position/buy', methods=['POST'])
def buy_stock():
    data = request.get_json()
    code = data.get('code', '').strip()
    price = float(data.get('price', 0))
    if not code or price <= 0:
        return jsonify({'error': '参数错误'}), 400
    positions = load_positions()
    if any(p['code'] == code for p in positions):
        return jsonify({'error': '已持有该股票'}), 400
    positions.append({
        'code': code,
        'buy_price': price,
        'buy_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'shares': 100
    })
    save_positions(positions)
    return jsonify({'ok': True, 'message': f'已记录买入 {code} @ ¥{price}'})

@app.route('/api/position/sell', methods=['POST'])
def sell_stock():
    data = request.get_json()
    code = data.get('code', '').strip()
    price = float(data.get('price', 0))
    if not code or price <= 0:
        return jsonify({'error': '参数错误'}), 400
    positions = load_positions()
    target = next((p for p in positions if p['code'] == code), None)
    if not target:
        return jsonify({'error': '未持仓该股票'}), 400
    profit = (price - target['buy_price']) * target['shares']
    trade = {
        'code': code,
        'buy_price': target['buy_price'],
        'sell_price': price,
        'buy_time': target['buy_time'],
        'sell_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'profit': round(profit, 2),
        'profit_pct': round((price - target['buy_price']) / target['buy_price'] * 100, 2)
    }
    trades = load_trades()
    trades.append(trade)
    save_trades(trades)
    positions.remove(target)
    save_positions(positions)
    return jsonify({'ok': True, 'trade': trade})
@app.route('/api/trades', methods=['GET'])
def get_trades():
    return jsonify(load_trades()[-50:])
@app.route('/api/stats', methods=['GET'])
def stats():
    return jsonify(calc_stats())

@app.route('/api/diary', methods=['GET'])
def get_diary():
    return jsonify(load_json(DIARY_FILE, [])[-30:])

@app.route('/api/diary', methods=['POST'])
def add_diary():
    data = request.get_json()
    text = data.get('text', '').strip()
    if not text:
        return jsonify({'error': '内容不能为空'}), 400
    entries = load_json(DIARY_FILE, [])
    entry = {
        'id': len(entries) + 1,
        'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'text': text,
        'reminder': extract_reminder(text)
    }
    entries.append(entry)
    save_json(entries, DIARY_FILE)
    return jsonify(entry)

@app.route('/api/diary/<int:eid>', methods=['DELETE'])
def delete_diary(eid):
    entries = load_json(DIARY_FILE, [])
    entries = [e for e in entries if e['id'] != eid]
    save_json(entries, DIARY_FILE)
    return jsonify({'ok': True})

def extract_reminder(text):
    keywords = {
        '止损': '严格执行止损纪律',
        '追高': '避免追高，等待回调',
        '仓位': '注意仓位管理',
        '贪婪': '克服贪婪，见好就收',
        '恐惧': '克服恐惧，按信号执行',
        '纪律': '坚守交易纪律',
        '冲动': '避免冲动交易',
        '耐心': '保持耐心，等待最佳时机',
        '回撤': '控制回撤，保住本金',
        '复盘': '坚持每日复盘',
    }
    reminders = [rm for kw, rm in keywords.items() if kw in text]
    return reminders[:3] if reminders else ['认真复盘，持续进步']

@app.route('/api/yiming', methods=['GET'])
def yiming():
    today = datetime.date.today()
    random.seed(today.year * 10000 + today.month * 100 + today.day)
    return jsonify(random.choice(YIMING_28))

@app.route('/api/pet_reminder', methods=['GET'])
def pet_reminder():
    entries = load_json(DIARY_FILE, [])
    if not entries:
        return jsonify({'text': '还没有复盘日记哦，写一条吧~ 🐶'})
    reminders = entries[-1].get('reminder', [])
    if reminders:
        return jsonify({'text': f"小橘子提醒：{random.choice(reminders)} 🐶"})
    return jsonify({'text': '今天也要坚持复盘哦~ 🐶'})

@app.route('/api/news', methods=['GET'])
def news():
    news_list = []
    try:
        express_news = ak.stock_info_global_em()
        if express_news is not None and len(express_news) > 0:
            for _, row in express_news.head(20).iterrows():
                title = str(row.get('title', ''))
                content = str(row.get('content', '')) if 'content' in row else ''
                sectors = analyze_news_sectors(title, content)
                news_list.append({
                    'title': title,
                    'time': str(row.get('datetime', '')),
                    'source': '快讯',
                    'content': content,
                    'sectors': sectors
                })
    except:
        pass
    try:
        today = datetime.date.today()
        wd = today.weekday()
        if wd >= 5:
            today = today - datetime.timedelta(days=wd - 4)
        cctv_news = ak.news_cctv(date=today.strftime('%Y%m%d'))
        if cctv_news is not None and len(cctv_news) > 0:
            for _, row in cctv_news.head(10).iterrows():
                title = str(row.get('title', ''))
                content = str(row.get('content', '')) if 'content' in row else ''
                sectors = analyze_news_sectors(title, content)
                news_list.append({
                    'title': title,
                    'time': today.strftime('%Y%m%d'),
                    'source': '新闻联播',
                    'content': content,
                    'sectors': sectors
                })
    except:
        pass
    seen_titles = set()
    unique_news = []
    for n in news_list:
        if n['title'] and n['title'] not in seen_titles and len(n['title']) > 3:
            seen_titles.add(n['title'])
            unique_news.append(n)
    unique_news.sort(key=lambda x: str(x.get('time', '')), reverse=True)
    return jsonify(unique_news[:25])

@app.route('/api/moneyflow', methods=['GET'])
def moneyflow():
    try:
        today = datetime.date.today()
        wd = today.weekday()
        if wd == 5:
            target_date = today - datetime.timedelta(days=1)
        elif wd == 6:
            target_date = today - datetime.timedelta(days=2)
        else:
            target_date = today
        df = ak.stock_board_industry_name_em()
        df = df[['板块名称', '涨跌幅', '换手率', '主力净流入']].dropna()
        df['涨跌幅'] = pd.to_numeric(df['涨跌幅'], errors='coerce')
        df['主力净流入'] = pd.to_numeric(df['主力净流入'], errors='coerce')
        df = df.dropna()
        top_in = df.nlargest(10, '主力净流入')
        top_out = df.nsmallest(10, '主力净流入')
        return jsonify({
            'date': str(target_date),
            'top_inflow': top_in.to_dict('records'),
            'top_outflow': top_out.to_dict('records')
        })
    except:
        return jsonify({'date': '', 'top_inflow': [], 'top_outflow': []})

@app.route('/api/heatmap', methods=['GET'])
def heatmap():
    try:
        today = datetime.date.today()
        wd = today.weekday()
        # 周末回退到最近交易日
        if wd == 5:
            target_date = today - datetime.timedelta(days=1)
        elif wd == 6:
            target_date = today - datetime.timedelta(days=2)
        else:
            target_date = today

        df = ak.stock_board_industry_name_em()
        if df is None or len(df) == 0:
            return jsonify([])

        df = df[['板块名称', '涨跌幅', '换手率']].dropna()
        df['涨跌幅'] = pd.to_numeric(df['涨跌幅'], errors='coerce')
        df = df.dropna(subset=['涨跌幅'])
        df = df.sort_values('涨跌幅', ascending=False)

        top20 = df.head(20)
        bottom5 = df.tail(5)
        result = pd.concat([top20, bottom5])

        return jsonify({
            'date': str(target_date),
            'data': result.to_dict('records')
        })
    except Exception as e:
        print(f"热力图获取失败: {e}")
        return jsonify({'date': '', 'data': []})
import os

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)