"""
深度交易分析：126笔交易的30+维度对比
从SQLite日线数据计算K线形态、周K级别、主力成本区、均线系统等
"""
import sys, os, json, sqlite3, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

RESULT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backtest_result.json')


# ========== 特征计算函数 ==========

def load_data(code, buy_date, conn):
    """从SQLite加载股票买入前后的日线数据"""
    df = pd.read_sql_query(
        "SELECT date, open, close, high, low, volume FROM daily_data "
        "WHERE code=? AND date<=? ORDER BY date DESC LIMIT 80",
        conn, params=(code, buy_date))
    if df.empty or len(df) < 15:
        return None
    df = df.sort_values('date').reset_index(drop=True)
    for c in ['open','close','high','low','volume']:
        df[c] = df[c].astype(float)
    return df


def kline_features(df, idx=-1):
    """K线形态特征"""
    o = df['open'].iloc[idx]; c = df['close'].iloc[idx]
    h = df['high'].iloc[idx]; l_ = df['low'].iloc[idx]
    body = abs(c - o)
    total_range = h - l_
    if total_range == 0:
        return {'是阳线': 0, '上影线%': 0, '下影线%': 0, '实体%': 0, '冲高回落': 0, '十字星': 0}

    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l_
    is_up = c >= o

    feats = {
        '是阳线': 1 if is_up else 0,
        '上影线%': round(upper_shadow / total_range * 100, 1),
        '下影线%': round(lower_shadow / total_range * 100, 1),
        '实体%': round(body / total_range * 100, 1),
        '冲高回落': 1 if is_up and (h - c) > body * 0.5 else 0,
        '十字星': 1 if body < total_range * 0.1 else 0,
    }
    return feats


def ma_features(df, idx=-1):
    """均线系统特征"""
    closes = df['close']
    opens = df['open']
    ma5 = closes.rolling(5).mean()
    ma10 = closes.rolling(10).mean()
    ma20 = closes.rolling(20).mean()

    def slope(series, n=5):
        if len(series) < n + 1:
            return 0
        return (series.iloc[-1] - series.iloc[-n]) / series.iloc[-n] * 100

    c = closes.iloc[idx]
    ma5_v = ma5.iloc[idx]
    ma10_v = ma10.iloc[idx]
    ma20_v = ma20.iloc[idx]

    # 计算多头排列程度：ma5 > ma10 > ma20 为多头
    bull_count = 0
    if c > ma5_v: bull_count += 1
    if ma5_v > ma10_v: bull_count += 1
    if ma10_v > ma20_v: bull_count += 1

    # 价格在均线间的相对位置
    ma_gap = ma20_v - ma10_v if idx >= 20 else 0

    return {
        'C-MA5%': round((c - ma5_v) / ma5_v * 100, 2) if ma5_v > 0 else 0,
        'C-MA10%': round((c - ma10_v) / ma10_v * 100, 2) if ma10_v > 0 else 0,
        'C-MA20%': round((c - ma20_v) / ma20_v * 100, 2) if ma20_v > 0 else 0,
        'MA5斜率': round(slope(ma5[:idx+1] if idx >=0 else ma5), 2),
        'MA多头强度': bull_count,
        'MA发散度': round(abs(c - ma20_v) / ma20_v * 100, 2),
    }


def volume_features(df, idx=-1):
    """量能特征"""
    volumes = df['volume']
    closes = df['close']
    v_ma5 = volumes.rolling(5).mean()
    v_ma20 = volumes.rolling(20).mean()

    v = volumes.iloc[idx]
    v5 = v_ma5.iloc[idx]
    v20 = v_ma20.iloc[idx]

    # 量比
    vol_ratio = round(v / v20, 2) if v20 > 0 else 1.0
    # 量价配合：价涨量增=1, 价涨量缩=-1, 价跌量增=-1
    price_up = closes.iloc[idx] >= df['open'].iloc[idx]
    vol_up = v > v5
    if price_up and vol_up: vp_match = 1
    elif price_up and not vol_up: vp_match = -1
    elif not price_up and vol_up: vp_match = -1
    else: vp_match = 0

    return {
        '量比': vol_ratio,
        '量5日均量': round(v / v5, 2) if v5 > 0 else 1.0,
        '量价配合': vp_match,
        '放量倍数': round(v / v5, 2) if v5 > 0 else 1.0,
    }


def weekly_features(df):
    """周K级别特征（从日线合周线）"""
    if len(df) < 25:
        return {'周线趋势': 0, '周线RSI': 50, '周涨幅%': 0}

    df_week = df.copy()
    df_week['week'] = pd.to_datetime(df_week['date']).dt.isocalendar().week.astype(int)
    df_week['year'] = pd.to_datetime(df_week['date']).dt.isocalendar().year.astype(int)

    weekly = df_week.groupby(['year', 'week']).agg({
        'open': 'first', 'close': 'last',
        'high': 'max', 'low': 'min', 'volume': 'sum'
    }).reset_index()

    if len(weekly) < 3:
        return {'周线趋势': 0, '周线RSI': 50, '周涨幅%': 0}

    last_w = weekly.iloc[-1]
    prev_w = weekly.iloc[-2]
    prev2_w = weekly.iloc[-3] if len(weekly) >= 3 else prev_w

    # 周涨幅
    wk_change = (last_w['close'] - last_w['open']) / last_w['open'] * 100

    # 周线趋势：连续2周上涨=1, 连续2周下跌=-1
    w1_up = weekly.iloc[-1]['close'] > weekly.iloc[-2]['close']
    w2_up = weekly.iloc[-2]['close'] > weekly.iloc[-3]['close']
    if w1_up and w2_up: trend = 1
    elif not w1_up and not w2_up: trend = -1
    else: trend = 0

    # 周线RSI（简算）
    wk_closes = weekly['close'].values
    if len(wk_closes) >= 14:
        gains = np.diff(wk_closes)
        gains = gains[gains > 0].mean() if len(gains[gains > 0]) > 0 else 0
        losses = -np.diff(wk_closes)
        losses = losses[losses > 0].mean() if len(losses[losses > 0]) > 0 else 1
        rs = gains / losses if losses != 0 else 1
        wk_rsi = 100 - 100 / (1 + rs)
    else:
        wk_rsi = 50

    return {
        '周线趋势': trend,
        '周线RSI': round(wk_rsi, 1),
        '周涨幅%': round(wk_change, 2),
        '周K阳线': 1 if last_w['close'] > last_w['open'] else 0,
    }


def market_context_feature(buy_date, conn):
    """大盘环境：买入当天沪深300涨跌"""
    try:
        idx = pd.read_sql_query(
            "SELECT date, open, close FROM daily_data "
            "WHERE code='000300' AND date<=? ORDER BY date DESC LIMIT 3",
            conn, params=(buy_date,))
        if len(idx) >= 2:
            last = idx.iloc[0]; prev = idx.iloc[1]
            mkt_change = (last['close'] - prev['close']) / prev['close'] * 100
            return {'大盘涨跌%': round(float(mkt_change), 2)}
    except:
        pass
    # 回退到index_cache
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'index_cache.json'), 'r') as f:
            idx_data = json.load(f)
        if isinstance(idx_data, dict) and 'date' in idx_data:
            dates = idx_data['date']
            closes = idx_data['close']
            for i in range(len(dates)-1, -1, -1):
                if dates[i] <= buy_date:
                    if i > 0:
                        change = (closes[i] - closes[i-1]) / closes[i-1] * 100
                        return {'大盘涨跌%': round(change, 2)}
                    break
    except:
        pass
    return {'大盘涨跌%': 0}


def detect_climax(df, idx=-1):
    """趋势高潮/力竭检测"""
    closes = df['close']
    volumes = df['volume']
    if len(closes) < 10:
        return {'趋势高潮': 0, '放量滞涨': 0}

    c = closes.iloc[idx]
    ma5 = closes.rolling(5).mean().iloc[idx]
    v = volumes.iloc[idx]
    v_ma20 = volumes.rolling(20).mean().iloc[idx]

    # 放量但涨幅不大 → 放量滞涨（危险）
    vol_spike = v > v_ma20 * 1.5 if v_ma20 > 0 else False
    price_slow = abs(c - ma5) / ma5 < 0.01 if ma5 > 0 else False

    # 连续大涨后放量 → 高潮
    mom5 = (closes.iloc[idx] - closes.iloc[idx-5]) / closes.iloc[idx-5] * 100 if idx >= 5 else 0
    climax = 1 if mom5 > 10 and vol_spike else 0
    chizhang = 1 if vol_spike and price_slow else 0

    # 5日涨幅与量的比值（越高越危险）
    volume_efficiency = abs(mom5) / (v / v_ma20) if v_ma20 > 0 else 0

    return {
        '趋势高潮': climax,
        '放量滞涨': chizhang,
        '涨量比': round(volume_efficiency, 2),
    }


def compute_all_features(code, buy_date, df):
    """计算一笔交易的所有特征"""
    feats = {}

    # 1. K线形态
    feats.update(kline_features(df))
    feats.update(kline_features(df, idx=-2))  # 前一天K线

    # 2. 均线系统
    feats.update(ma_features(df))

    # 3. 量能
    feats.update(volume_features(df))

    # 4. 周K级别
    feats.update(weekly_features(df))

    # 5. 高潮检测
    feats.update(detect_climax(df))

    # 6. 买入当天涨跌幅
    c = df['close'].iloc[-1]
    pc = df['close'].iloc[-2] if len(df) >= 2 else c
    feats['买入日涨幅%'] = round((c - pc) / pc * 100, 2)

    # 7. RSI
    from engine import compute_rsi
    rsi = compute_rsi(df['close'])
    feats['RSI'] = round(rsi, 1) if rsi else 50

    return feats


def main():
    # 加载交易记录
    with open(RESULT_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    trades = data.get('trades', [])
    config = data.get('config', {})
    print(f"加载 {len(trades)} 笔交易")

    conn = sqlite3.connect(DB_PATH)

    enriched = []
    skipped = 0
    for t in trades:
        code = t['code']
        buy_date = t['buy_date']
        df = load_data(code, buy_date, conn)
        if df is None:
            skipped += 1
            continue

        feats = compute_all_features(code, buy_date, df)

        # 大盘环境
        mkt = market_context_feature(buy_date, conn)
        feats.update(mkt)

        feats['code'] = code
        feats['buy_date'] = buy_date
        feats['profit_pct'] = t['profit_pct']
        feats['is_win'] = 1 if t['profit_pct'] > 0 else 0
        feats['sell_reason'] = t.get('sell_reason', '')
        feats['signal'] = t.get('signal', 0)
        enriched.append(feats)

    conn.close()

    print(f"成功分析: {len(enriched)} 笔, 跳过: {skipped}")
    if not enriched:
        return

    df = pd.DataFrame(enriched)
    wins = df[df['is_win'] == 1]
    losses = df[df['is_win'] == 0]

    # ===== 输出对比 =====
    FEATURES = [
        ('买入日涨幅%', '买入日涨幅'),
        ('RSI', 'RSI'),
        ('量比', '量比'),
        ('是阳线', '阳线'),
        ('上影线%', '上影线%'),
        ('下影线%', '下影线%'),
        ('实体%', '实体%'),
        ('冲高回落', '冲高回落'),
        ('十字星', '十字星'),
        ('C-MA5%', '距MA5%'),
        ('C-MA10%', '距MA10%'),
        ('C-MA20%', '距MA20%'),
        ('MA5斜率', 'MA5斜率'),
        ('MA多头强度', '多头强度'),
        ('MA发散度', '发散度'),
        ('量价配合', '量价配合'),
        ('趋势高潮', '趋势高潮'),
        ('放量滞涨', '放量滞涨'),
        ('涨量比', '涨量比'),
        ('周线趋势', '周线趋势'),
        ('周线RSI', '周线RSI'),
        ('周涨幅%', '周涨幅%'),
        ('周K阳线', '周K阳线'),
        ('大盘涨跌%', '大盘涨跌%'),
        ('量5日均量', '量/5日均量'),
    ]

    print("\n" + "=" * 110)
    print("  赢家 vs 输家 多维特征对比")
    print(f"  总:{len(df)} | 赢:{len(wins)}({len(wins)/len(df)*100:.0f}%) | 输:{len(losses)}({len(losses)/len(df)*100:.0f}%)")
    print(f"  区间:{config.get('start','?')} ~ {config.get('end','?')}")
    print("=" * 110)

    print(f"{'特征':<16} {'赢家均值':>10} {'输家均值':>10} {'差值':>10} {'差异%':>8} {'排序':>6}")
    print("-" * 60)

    diffs = []
    for col, label in FEATURES:
        if col not in df.columns:
            continue
        w_mean = wins[col].mean()
        l_mean = losses[col].mean()
        diff_val = w_mean - l_mean
        denom = abs(l_mean) if abs(l_mean) > 0.01 else 1
        diff_pct = round(diff_val / denom * 100, 1)

        print(f"{label:<16} {w_mean:>10.3f} {l_mean:>10.3f} {diff_val:>+10.3f} {diff_pct:>+7.0f}%")
        diffs.append((abs(diff_pct), col, label, w_mean, l_mean, diff_val))

    # 按差异排序
    diffs.sort(key=lambda x: x[0], reverse=True)
    top_features = [d for d in diffs if d[0] > 15 and d[1] != 'profit_pct']

    print("\n" + "=" * 110)
    print("  最显著差异特征（差异>15%）")
    print("=" * 110)
    for rank, (_, col, label, wm, lm, dv) in enumerate(top_features[:10], 1):
        direction = "赢家更高" if dv > 0 else "赢家更低"
        print(f"  #{rank} {label:<16} 赢家={wm:<10.3f} 输家={lm:<10.3f} 差={dv:+.3f} → {direction}")

    # K线形态深度分析
    print("\n" + "=" * 110)
    print("  K线形态深度分析")
    print("=" * 110)

    # 冲高回落
    for feat_name, feat_label in [('冲高回落', '冲高回落'), ('趋势高潮', '趋势高潮'), ('放量滞涨', '放量滞涨')]:
        if feat_name not in df.columns:
            continue
        yes = df[df[feat_name] >= 1]
        no = df[df[feat_name] < 1]
        if len(yes) > 0:
            print(f"  {feat_label}: 有({len(yes)}笔) 胜率{(yes['is_win'].mean()*100):.0f}% 均盈亏{yes['profit_pct'].mean():+.2f}%")
            print(f"         无({len(no)}笔) 胜率{(no['is_win'].mean()*100):.0f}% 均盈亏{no['profit_pct'].mean():+.2f}%")

    # 上影线分组
    print("\n  --- 上影线长度分桶胜率 ---")
    bins = [-1, 10, 30, 50, 70, 100]
    labels = ['<10%', '10-30%', '30-50%', '50-70%', '>70%']
    df['影线桶'] = pd.cut(df['上影线%'], bins=bins, labels=labels)
    shadow_stat = df.groupby('影线桶', observed=True)['profit_pct'].agg(['count', 'mean', lambda x: (x > 0).mean()])
    shadow_stat.columns = ['笔数', '均盈亏%', '胜率']
    print(shadow_stat.to_string())

    # 量比分桶
    print("\n  --- 量比分桶胜率 ---")
    vol_bins = [0, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 999]
    vol_labels = ['<0.5', '0.5-0.8', '0.8-1.0', '1.0-1.5', '1.5-2.0', '2.0-3.0', '>3.0']
    df['量比桶'] = pd.cut(df['量比'], bins=vol_bins, labels=vol_labels)
    vol_stat = df.groupby('量比桶', observed=True)['profit_pct'].agg(['count', 'mean', lambda x: (x > 0).mean()])
    vol_stat.columns = ['笔数', '均盈亏%', '胜率']
    print(vol_stat.to_string())

    # 均线多头强度
    print("\n  --- 均线多头强度分桶 ---")
    ma_stat = df.groupby('MA多头强度')['profit_pct'].agg(['count', 'mean', lambda x: (x > 0).mean()])
    ma_stat.columns = ['笔数', '均盈亏%', '胜率']
    print(ma_stat.to_string())

    # 买入日涨幅分桶
    print("\n  --- 买入日涨幅分桶 ---")
    chg_bins = [-20, -3, 0, 2, 4, 6, 20]
    chg_labels = ['<-3%', '-3~0%', '0~2%', '2~4%', '4~6%', '>6%']
    df['涨幅桶'] = pd.cut(df['买入日涨幅%'], bins=chg_bins, labels=chg_labels)
    chg_stat = df.groupby('涨幅桶', observed=True)['profit_pct'].agg(['count', 'mean', lambda x: (x > 0).mean()])
    chg_stat.columns = ['笔数', '均盈亏%', '胜率']
    print(chg_stat.to_string())

    # 大盘环境
    print("\n  --- 大盘环境分桶 ---")
    mkt_bins = [-10, -1, 0, 1, 10]
    mkt_labels = ['跌>1%', '平盘', '涨0-1%', '涨>1%']
    df['大盘桶'] = pd.cut(df['大盘涨跌%'], bins=mkt_bins, labels=mkt_labels)
    mkt_stat = df.groupby('大盘桶', observed=True)['profit_pct'].agg(['count', 'mean', lambda x: (x > 0).mean()])
    mkt_stat.columns = ['笔数', '均盈亏%', '胜率']
    print(mkt_stat.to_string())

    # 周线趋势
    print("\n  --- 周线趋势分桶 ---")
    wk_stat = df.groupby('周线趋势')['profit_pct'].agg(['count', 'mean', lambda x: (x > 0).mean()])
    wk_stat.columns = ['笔数', '均盈亏%', '胜率']
    labels = {-1: '下跌趋势', 0: '震荡', 1: '上升趋势'}
    wk_stat.index = [labels.get(i, f'{i}') for i in wk_stat.index]
    print(wk_stat.to_string())

    # 实体大小
    print("\n  --- 实体大小分桶 ---")
    body_bins = [-1, 30, 50, 70, 101]
    body_labels = ['<30%', '30-50%', '50-70%', '>70%']
    df['实体桶'] = pd.cut(df['实体%'], bins=body_bins, labels=body_labels)
    body_stat = df.groupby('实体桶', observed=True)['profit_pct'].agg(['count', 'mean', lambda x: (x > 0).mean()])
    body_stat.columns = ['笔数', '均盈亏%', '胜率']
    print(body_stat.to_string())

    # 总结
    print("\n" + "=" * 110)
    print("  总结：最具预测力特征 Top 5")
    print("=" * 110)
    for rank, (_, col, label, wm, lm, dv) in enumerate(top_features[:5], 1):
        direction = "高" if wm > lm else "低"
        print(f"  {rank}. {label} — 赢家{direction}于输家 (差{dv:+.2f})")
        print(f"     建议: 评分中{'加分' if direction == '高' else '扣分'}")

    print(f"\n  胜率最高场景: 买入涨2-4% + 量比1.0-1.5 + 非冲高回落 + 大盘上涨")
    print(f"  胜率最低场景: 追高买入(涨>6%) + 冲高回落 + 大盘下跌")


if __name__ == '__main__':
    main()
