"""形态识别引擎：从日线数据中自动识别经典K线形态"""
import sys, sqlite3, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
import pandas as pd
import numpy as np
from config import DB_PATH
import warnings; warnings.filterwarnings('ignore')


def load_stock_data(code, min_days=60):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT date, open, close, high, low, volume FROM daily_data WHERE code=? ORDER BY date",
        conn, params=(code,))
    conn.close()
    if df.empty or len(df) < min_days: return None
    for c in ['open','close','high','low','volume']: df[c] = df[c].astype(float)
    df['date'] = pd.to_datetime(df['date'], format='mixed')
    return df


def detect_w_bottom(df, lookback=30):
    """W底：两个相近低点，中间反弹，突破颈线"""
    recent = df.tail(lookback).copy()
    if len(recent) < 20: return {'found': False, 'score': 0}
    closes, highs, lows, vols = recent['close'].values, recent['high'].values, recent['low'].values, recent['volume'].values
    bottoms = []
    for i in range(2, len(recent)-2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            bottoms.append((i, lows[i], vols[i]))
    if len(bottoms) < 2: return {'found': False, 'score': 0}
    best = {'found': False, 'score': 0}
    for i in range(len(bottoms)):
        for j in range(i+1, len(bottoms)):
            li, lv1, v1 = bottoms[i]
            lj, lv2, v2 = bottoms[j]
            gap = lj - li
            if gap < 5 or gap > 20: continue
            ld = abs(lv2 - lv1) / max(lv1, lv2)
            if ld > 0.15: continue
            mid_h = max(highs[li:lj+1])
            avg_l = (lv1 + lv2) / 2
            bounce = (mid_h - avg_l) / avg_l
            if bounce < 0.05: continue
            vr = v2 / v1 if v1 > 0 else 1
            neck = mid_h
            cur = closes[-1]
            if cur > neck:
                bi = np.argmax(highs[li:lj+1]) + li
                pb = min(lows[bi:]) if bi < len(lows) else cur
                pb_pct = (neck - pb) / neck * 100
                score = 0
                if vr < 1.2: score += 20
                if bounce > 0.10: score += 15
                if ld < 0.05: score += 15
                if pb_pct < 3 and pb_pct > -1: score += 25
                elif cur > neck * 1.03: score += 20
                if vr < 0.8: score += 15
                if score > best['score']:
                    best = {'found': True, 'score': min(score, 80),
                            'left_low': str(recent.iloc[li]['date'])[:10],
                            'right_low': str(recent.iloc[lj]['date'])[:10],
                            'neckline': round(neck,2),
                            'current_vs_neckline': round((cur/neck-1)*100,1)}
    return best


def detect_head_shoulders(df, lookback=40):
    """头肩底：左肩→头部(最低)→右肩→突破颈线"""
    recent = df.tail(lookback).copy()
    if len(recent) < 20: return {'found': False, 'score': 0}
    lows, highs, closes = recent['low'].values, recent['high'].values, recent['close'].values
    bottoms = []
    for i in range(3, len(recent)-3):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            bottoms.append((i, lows[i]))
    if len(bottoms) < 3: return {'found': False, 'score': 0}
    best = {'found': False, 'score': 0}
    for i in range(len(bottoms)-2):
        li, lv = bottoms[i]
        hi, hv = bottoms[i+1]
        ri, rv = bottoms[i+2]
        if not (3 <= hi-li <= 15 and 3 <= ri-hi <= 15): continue
        if not (hv < lv and hv < rv): continue
        sd = abs(lv-rv)/max(lv,rv)
        if sd > 0.10: continue
        hd = min((lv-hv)/lv, (rv-hv)/rv)
        if hd < 0.03: continue
        neck = min(max(highs[li:hi+1]), max(highs[hi:ri+1]))
        if closes[-1] > neck:
            s = min(80, int(20 + hd*200 + (1-sd)*30))
            if s > best['score']:
                best = {'found': True, 'score': s, 'neckline': round(neck,2),
                        'current_vs_neckline': round((closes[-1]/neck-1)*100,1)}
    return best


def detect_box_breakout(df, lookback=20):
    """箱体突破：横盘>=5天→放量突破"""
    recent = df.tail(lookback).copy()
    if len(recent) < 10: return {'found': False, 'score': 0}
    h, l, c, v = recent['high'].values, recent['low'].values, recent['close'].values, recent['volume'].values
    bh, bl = max(h[-10:-1]), min(l[-10:-1])
    amp = (bh - bl) / bl * 100
    if amp > 15: return {'found': False, 'score': 0}
    if c[-1] > bh and v[-1] > np.mean(v[-10:-1]) * 1.5:
        return {'found': True, 'score': 50, 'box_high': round(bh,2), 'amplitude': round(amp,1)}
    return {'found': False, 'score': 0}


def detect_macd_divergence(df):
    """MACD底背离：股价新低但MACD没新低

    本地计算MACD全序列（不依赖engine.py的compute_macd，那个只返float）
    """
    if len(df) < 40: return {'found': False, 'score': 0}
    close = df['close']
    # 本地算MACD全序列
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    dif = ema12 - ema26                # 全序列
    dea = dif.ewm(span=9).mean()       # 全序列
    macd_hist = dif - dea              # 全序列（Series）

    if len(macd_hist) < 30:
        return {'found': False, 'score': 0}

    # 找最近20根K线的价格最低点
    recent = close.iloc[-25:]
    recent_hist = macd_hist.iloc[-25:]
    price_min_idx = recent.idxmin()
    pos = list(recent.index).index(price_min_idx)

    if pos < len(recent) - 2:  # 不是最后两根
        price_low = recent.iloc[pos]
        macd_at_low = recent_hist.iloc[pos]

        # 找前一个低点（验证当前确是新低）
        earlier = close.iloc[-50:-25]
        if len(earlier) > 5:
            prev_low = earlier.min()
            if price_low >= prev_low * 0.98:  # 没创新低，不算背离
                return {'found': False, 'score': 0}

        macd_now = recent_hist.iloc[-1]

        # 条件1: MACD已回升至少15%（不再创新低）
        # 条件2: 当前MACD为正
        cond1 = macd_at_low < 0 and macd_now > macd_at_low * 1.15
        cond2 = macd_hist.iloc[-5:].mean() > macd_hist.iloc[:5].mean()  # 5日趋势向上

        if cond1 and cond2:
            score = 30
            # 加分：macd回升幅度大
            if macd_now > 0:
                score += 10
            if pos >= 5:  # 低点在5根K线前（有足够时间确认）
                score += 10
            return {'found': True, 'score': min(score, 50),
                    'divergence_date': str(price_min_idx)[:10],
                    'price_low': round(price_low, 2)}
    return {'found': False, 'score': 0}


def detect_bull_cannon(df):
    """多方炮：阳-阴-阳三根，中间缩量

    放宽版：阳-阴-阳形态（不严格要求仅看最后3根），
    中间阴线缩量（不苛求<80%，<100%即可）
    """
    if len(df) < 5: return {'found': False, 'score': 0}
    c = df['close'].values
    o = df['open'].values
    v = df['volume'].values

    # 扫描最近10根K线找阳-阴-阳
    for i in range(-3, -min(11, len(c)), -1):
        if i - 2 < -len(c):
            break
        if c[i] > o[i] and c[i-1] < o[i-1] and c[i-2] > o[i-2]:
            # 中间阴线缩量（相对前阳）
            if v[i-1] < v[i-2] * 1.0:  # 不严格缩量，只要不超过就行
                if c[i] > c[i-2]:  # 最后一阳收盘高于第一阳
                    score = 25
                    # 加分项
                    if v[i-1] < v[i-2] * 0.7:
                        score += 10  # 明显缩量加分
                    if c[i] > max(o[i], c[i-1]) * 1.02:
                        score += 10  # 强势突破加分
                    return {'found': True, 'score': min(score, 45)}
    return {'found': False, 'score': 0}




def detect_d1_setup(df):
    """D1放量大阳线模式：放量+大阳+强势收盘→明日观察池"""
    if len(df) < 25:
        return {'found': False, 'score': 0}
    d1 = df.iloc[-2]  # 昨天是D1（今天可能是D2，不作为判断依据）
    d1_chg = (d1['close'] / d1['open'] - 1) * 100
    if d1_chg < 5 or d1['close'] <= d1['open']:
        return {'found': False, 'score': 0}
    avg_vol = df['volume'].iloc[-21:-1].mean() if len(df) >= 21 else df['volume'].mean()
    vr = d1['volume'] / max(avg_vol, 1)
    if vr < 1.3:
        return {'found': False, 'score': 0}
    d1_range = d1['high'] - d1['low']
    close_pos = (d1['close'] - d1['low']) / d1_range if d1_range > 0 else 0.5
    if close_pos < 0.6:
        return {'found': False, 'score': 0}
    score = min(85, int(30 + d1_chg * 3 + (close_pos - 0.6) * 50))
    return {
        'found': True, 'score': score,
        'd1_date': str(d1['date'])[:8] if not hasattr(d1['date'], 'strftime') else d1['date'].strftime('%Y%m%d'),
        'd1_open': round(d1['open'], 2), 'd1_close': round(d1['close'], 2),
        'd1_chg': round(d1_chg, 1), 'd1_volume_ratio': round(vr, 2),
    }


def detect_d2_confirmation(df, d1_open, d1_close):
    """D2确认买入：低开+回踩D1开盘价+阳线反包"""
    if len(df) < 2:
        return {'found': False, 'score': 0}
    today = df.iloc[-1]
    yesterday = df.iloc[-2]
    d2_open_chg = (today['open'] / yesterday['close'] - 1) * 100
    if d2_open_chg > -1.5:
        return {'found': False, 'score': 0}
    if today['low'] > d1_open * 1.04:
        return {'found': False, 'score': 0}
    if today['close'] <= today['open'] or today['close'] <= yesterday['close']:
        return {'found': False, 'score': 0}
    return {
        'found': True, 'score': 75,
        'entry_price': round(max(d1_open, today['low']), 2),
        'd2_open_chg': round(d2_open_chg, 1),
    }


def scan_d1_candidates(max_stocks=500):
    """全市场扫描D1模式→生成明日观察池"""
    import datetime as _dt
    conn = sqlite3.connect(DB_PATH)
    codes = [r[0] for r in conn.execute(
        "SELECT code FROM daily_data GROUP BY code HAVING COUNT(*) > 40"
    ).fetchall()[:max_stocks]]
    conn.close()
    today = _dt.date.today()
    results = []
    for code in codes:
        df = load_stock_data(code)
        if df is None or len(df) < 25:
            continue
        try:
            # 检查最新K线日期，必须是今天或昨天
            latest_date = df['date'].iloc[-1]
            if isinstance(latest_date, str):
                latest_dt = _dt.datetime.strptime(str(latest_date)[:8], '%Y%m%d').date()
            elif hasattr(latest_date, 'date'):
                latest_dt = latest_date.date()
            else:
                latest_dt = _dt.datetime.strptime(str(latest_date)[:10].replace('-','')[:8], '%Y%m%d').date()

            # 缓存超过1天 → 尝试刷新
            if (today - latest_dt).days > 1:
                try:
                    from data import get_stock_daily_cached
                    get_stock_daily_cached(code, days=60, force_refresh=True)
                    df = load_stock_data(code)
                    if df is not None and len(df) >= 25:
                        latest_date = df['date'].iloc[-1]
                        if isinstance(latest_date, str):
                            latest_dt = _dt.datetime.strptime(str(latest_date)[:8], '%Y%m%d').date()
                        elif hasattr(latest_date, 'date'):
                            latest_dt = latest_date.date()
                except:
                    pass
                # 刷新后再检查，还是旧数据就跳过
                if (today - latest_dt).days > 1:
                    continue

            r = detect_d1_setup(df)
            if r['found'] and r['score'] >= 25:
                results.append((code, r['score'], 'D1放量大阳', r))
        except:
            pass
    results.sort(key=lambda x: -x[1])
    return results


def scan_all_stocks(max_stocks=500):
    """扫描全市场，返回所有形态评分最高的股票"""
    conn = sqlite3.connect(DB_PATH)
    codes = [r[0] for r in conn.execute(
        "SELECT code FROM daily_data GROUP BY code HAVING COUNT(*) > 40 ORDER BY COUNT(*) DESC"
    ).fetchall()[:max_stocks]]
    conn.close()
    results = []
    for code in codes:
        df = load_stock_data(code)
        if df is None: continue
        dets = [detect_w_bottom, detect_head_shoulders, detect_box_breakout,
                detect_macd_divergence, detect_bull_cannon]
        for d in dets:
            try:
                r = d(df)
                if r['found'] and r['score'] >= 25:
                    results.append((code, r['score'], d.__name__.replace('detect_',''), r))
            except: pass
    results.sort(key=lambda x: -x[1])
    return results


if __name__ == '__main__':
    import warnings; warnings.filterwarnings('ignore')
    for code in ['001896','000001','600519','000002','300750']:
        df = load_stock_data(code)
        if df is None: continue
        parts = [code]
        for d in [detect_w_bottom, detect_head_shoulders, detect_box_breakout, detect_macd_divergence, detect_bull_cannon]:
            r = d(df)
            parts.append(f'{d.__name__.replace("detect_","")}={r["found"]}({r["score"]})')
        print(' '.join(parts))
    print('\n全市场扫描TOP10:')
    for code, score, pname, detail in scan_all_stocks(300)[:10]:
        print(f'{code}: {pname} score={score}')
