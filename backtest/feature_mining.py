"""
D1D2特征挖掘：用历史信号数据找赚钱特征
"""
import sys, os, json, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== 对每笔D1D2信号提取特征 ==========

def extract_features(code, d1_idx, d2_idx, df):
    """对一笔D1D2交易提取全部特征"""
    features = {}
    try:
        d1 = df.iloc[d1_idx]
        d2 = df.iloc[d2_idx]
        d1_close = float(d1['close'])
        d1_open = float(d1['open'])
        d2_close = float(d2['close'])
        d2_low = float(d2['low'])

        # === D1特征 ===
        d1_chg = (d1_close / d1_open - 1) * 100
        d1_range = float(d1['high']) - float(d1['low'])

        # D1收盘位置（强势程度）
        close_pos = (d1_close - float(d1['low'])) / d1_range if d1_range > 0 else 0.5

        # D1量比
        lookback = min(20, d1_idx)
        avg_vol = df.iloc[d1_idx-lookback:d1_idx]['volume'].mean()
        d1_vr = float(d1['volume']) / max(float(avg_vol), 1)

        # D1前5天走势（蓄势还是平地起）
        pre5 = df.iloc[max(0,d1_idx-6):d1_idx]
        pre5_chg = sum(abs(float(pre5.iloc[i]['close']) / float(pre5.iloc[i]['open']) - 1) * 100
                       for i in range(len(pre5)))
        pre5_avg_chg = pre5_chg / len(pre5) if len(pre5) > 0 else 1
        has_accumulation = pre5_avg_chg < 3 and len(pre5) >= 3  # 前3天波动<3% = 蓄势

        # D1成交量突变程度
        pre20_vol = df.iloc[max(0,d1_idx-21):d1_idx-1]['volume'].mean()
        d1_vol_surge = float(d1['volume']) / max(float(pre20_vol), 1)

        # === D2特征 ===
        d2_chg = (d2_close / float(d2['open']) - 1) * 100

        # D2量比
        d2_avg_vol = df.iloc[max(0,d2_idx-21):d2_idx-1]['volume'].mean()
        d2_vr = float(d2['volume']) / max(float(d2_avg_vol), 1) if d2_avg_vol > 0 else 0

        # 缩量程度（D2 vs D1）
        vol_shrink = float(d2['volume']) / max(float(d1['volume']), 1)

        # D2换手率变化（用成交量占比近似）
        d2_vol_vs_20avg = float(d2['volume']) / max(float(pre20_vol), 1)

        # 价格相对均线
        ma5 = df['close'].rolling(5).mean().iloc[d2_idx] if d2_idx >= 4 else d2_close
        ma10 = df['close'].rolling(10).mean().iloc[d2_idx] if d2_idx >= 9 else d2_close
        ma20 = df['close'].rolling(20).mean().iloc[d2_idx] if d2_idx >= 19 else d2_close
        price_vs_ma5 = d2_close / float(ma5) - 1  # 正=在均线上方
        price_vs_ma10 = d2_close / float(ma10) - 1
        price_vs_ma20 = d2_close / float(ma20) - 1

        # 多头排列 (MA5 > MA10 > MA20)
        multi_head = 1 if float(ma5) > float(ma10) > float(ma20) else 0

        # 回踩是否到均线
        d1_to_ma5 = float(d1_close) / float(ma5) - 1 if d2_idx >= 4 else 0
        pullback_to_ma = 1 if d2_low <= float(ma5) else 0

        # 回撤深度
        pullback = (d1_close - d2_close) / d1_close * 100

        # === 大盘特征（用池中均值近似）===
        # 从DataFrame无法获取大盘数据，后面单独补充

        features = {
            # D1
            'd1_chg': round(d1_chg, 1),
            'd1_vr': round(d1_vr, 2),
            'd1_close_pos': round(close_pos, 2),
            'd1_vol_surge': round(d1_vol_surge, 1),
            'has_accumulation': int(has_accumulation),
            'pre5_avg_chg': round(pre5_avg_chg, 2),
            # D2
            'd2_chg': round(d2_chg, 1),
            'd2_vr': round(d2_vr, 2),
            'd2_pullback': round(pullback, 1),
            'vol_shrink_ratio': round(vol_shrink, 2),
            'd2_vol_vs_20avg': round(d2_vol_vs_20avg, 1),
            # 价格结构
            'price_vs_ma5_pct': round(price_vs_ma5 * 100, 1),
            'price_vs_ma10_pct': round(price_vs_ma10 * 100, 1),
            'price_vs_ma20_pct': round(price_vs_ma20 * 100, 1),
            'multi_head': multi_head,
            'pullback_to_ma': pullback_to_ma,
        }
    except:
        pass
    return features


def process_stock(code):
    """全量扫描一只股票，返回所有D1D2信号及特征"""
    import akshare as ak
    signals = []
    try:
        df = ak.stock_zh_a_hist(symbol=code, start_date='20240101',
                                 end_date=datetime.now().strftime('%Y%m%d'),
                                 adjust="qfq")
        if df is None or len(df) < 60:
            return signals
        col_map = {'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'volume'}
        df = df.rename(columns={c: col_map[c] for c in df.columns if c in col_map})
        for c in ['open','close','high','low','volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['open','close','high','low','volume'])

        idx = 2
        while idx < len(df):
            try:
                d1 = df.iloc[idx-1]
                d2 = df.iloc[idx]
                d1_chg = (d1['close'] / d1['open'] - 1) * 100
                if d1_chg >= 5 and d1['close'] > d1['open']:
                    lookback = min(20, idx)
                    avg_vol = df.iloc[idx-1-lookback:idx-1]['volume'].mean()
                    vr = d1['volume'] / avg_vol if avg_vol > 0 else 2
                    d1_r = d1['high'] - d1['low']
                    cp = (d1['close'] - d1['low']) / d1_r if d1_r > 0 else 0.5
                    if vr >= 1.3 and cp >= 0.6:
                        pb = (d1['close'] - d2['close']) / d1['close'] * 100
                        if 1 <= pb <= 8 and d2['close'] > d2['open'] and d2['close'] >= d2['low'] * 1.003:
                            feats = extract_features(code, idx-1, idx, df)
                            # T+1表现
                            if idx + 1 < len(df):
                                pnl = (float(df.iloc[idx+1]['close']) / max(d2['low'] * 1.003, d1['open'] * 0.97) - 1) * 100
                            else:
                                pnl = 0
                            signals.append({
                                'code': code,
                                'd1_date': str(d1['date'])[:10],
                                'd2_date': str(d2['date'])[:10],
                                't1_pnl': round(pnl, 2),
                                'is_win': pnl > 0,
                                **feats
                            })
                            idx += 5  # 跳过防重叠
                            continue
            except:
                pass
            idx += 1
    except:
        pass
    return signals


def get_all_codes():
    import akshare as ak
    codes = set()
    try:
        df = ak.stock_zh_a_spot_em()
        if len(df) > 1000:
            codes.update(df['代码'].astype(str).str.zfill(6).tolist())
    except:
        pass
    if len(codes) < 2000:
        for func in [
            lambda: ak.stock_info_sh_name_code(symbol="主板A股"),
            lambda: ak.stock_info_sz_name_code(symbol="A股列表"),
            lambda: ak.stock_info_sh_name_code(symbol="科创板"),
        ]:
            try:
                df = func()
                if len(df) > 0:
                    codes.update(df.iloc[:, 0].astype(str).str.zfill(6).tolist())
            except:
                pass
    return sorted([c for c in codes if c.startswith(('0','3','6')) and not c.startswith(('300','301','688','689','8','4'))])


def analyze():
    t0 = datetime.now()
    codes = get_all_codes()
    print(f'全市场D1D2特征分析 {t0.strftime("%Y-%m-%d %H:%M")}')
    print(f'股票池: {len(codes)}只')

    all_signals = []
    done = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(process_stock, c): c for c in codes}
        for f in as_completed(futures):
            done += 1
            try:
                sigs = f.result()
                if sigs:
                    all_signals.extend(sigs)
            except:
                pass
            if done % 500 == 0:
                print(f'  [{done}/{len(codes)}] {len(all_signals)}信号')

    elapsed = (datetime.now() - t0).total_seconds()
    print(f'\n完成! {len(codes)}只, {len(all_signals)}笔T+1信号, 耗时{elapsed:.0f}s\n')

    if not all_signals:
        print('无信号')
        return

    df = pd.DataFrame(all_signals)

    # === 特征分析 ===
    print('='*60)
    print('特征挖掘报告')
    print(f'总信号: {len(df)}, 胜率: {df["is_win"].mean()*100:.1f}%')
    print('='*60)

    # 连续特征：分桶看胜率
    numeric_features = [
        ('d1_chg', 'D1涨幅%', [5, 7, 10, 15]),
        ('d1_vr', 'D1量比', [1.3, 1.5, 2, 3, 5]),
        ('d1_close_pos', 'D1收盘位置', [0.6, 0.7, 0.8, 0.9]),
        ('d1_vol_surge', 'D1成交量倍数', [2, 3, 5, 10]),
        ('d2_pullback', 'D2回撤%', [1, 2, 3, 5]),
        ('d2_vr', 'D2量比', [0, 0.5, 0.8, 1.0, 1.5]),
        ('vol_shrink_ratio', '缩量程度(D2/D1)', [0, 0.3, 0.5, 0.8, 1.2]),
        ('d2_vol_vs_20avg', 'D2量/20日均量', [0, 0.5, 0.8, 1.0, 1.5]),
        ('price_vs_ma5_pct', '距MA5%', [-5, -2, 0, 2, 5]),
        ('price_vs_ma10_pct', '距MA10%', [-5, -2, 0, 2, 5]),
        ('price_vs_ma20_pct', '距MA20%', [-5, -2, 0, 2, 5]),
    ]

    for col, name, thresholds in numeric_features:
        if col not in df.columns:
            continue
        print(f'\n▶ {name}')
        for i in range(len(thresholds)):
            if i == 0:
                sub = df[df[col] <= thresholds[0]]
                label = f'≤{thresholds[0]}'
            else:
                sub = df[(df[col] > thresholds[i-1]) & (df[col] <= thresholds[i])]
                label = f'{thresholds[i-1]}~{thresholds[i]}'
            if i == len(thresholds) - 1:
                sub = df[df[col] > thresholds[i]]
                label = f'≥{thresholds[i]}'
            if len(sub) >= 3:
                print(f'  {label:>8}: {len(sub):4d}笔  胜率{sub["is_win"].mean()*100:5.1f}%  均{sub["t1_pnl"].mean():+.2f}%')

    # 二值特征
    binary_features = [
        ('has_accumulation', 'D1前有蓄势'),
        ('multi_head', '多头排列'),
        ('pullback_to_ma', '回踩到MA5'),
    ]
    print('\n▶ 二值特征')
    for col, name in binary_features:
        if col not in df.columns:
            continue
        for val, label in [(1, '是'), (0, '否')]:
            sub = df[df[col] == val]
            if len(sub) >= 3:
                print(f'  {name}={label}: {len(sub):3d}笔  胜率{sub["is_win"].mean()*100:5.1f}%  均{sub["t1_pnl"].mean():+.2f}%')

    # 多因子组合分析
    print('\n▶ 多因子组合（前3最佳组合）')
    combos = []
    for vr in [(0.8, 1.5), (0.5, 1.0), (1.0, 2.0)]:
        for pb in [(1, 3), (2, 5), (1, 5)]:
            for ma in [0, 1]:
                sub = df[(df['d2_vr'] >= vr[0]) & (df['d2_vr'] <= vr[1]) &
                         (df['d2_pullback'] >= pb[0]) & (df['d2_pullback'] <= pb[1])]
                if ma == 1:
                    sub = sub[sub['multi_head'] == 1]
                if len(sub) >= 5:
                    ma_label = ' 多头' if ma else ''
                    combos.append({
                        'label': '量比' + str(vr[0]) + '-' + str(vr[1]) + ' 回撤' + str(pb[0]) + '-' + str(pb[1]) + ma_label,
                        'n': len(sub),
                        'wr': sub['is_win'].mean() * 100,
                        'avg': sub['t1_pnl'].mean(),
                    })
    combos.sort(key=lambda x: -x['wr'])
    for c in combos[:5]:
        lbl = c['label']; nn = c['n']; wr = c['wr']; avg = c['avg']
        print(f'  {lbl:>30}: {nn:3d}笔  胜率{wr:5.1f}%  均{avg:+.2f}%')

    # 保存
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'feature_analysis.json')
    df.to_json(out, orient='records', force_ascii=False)
    print(f'\n已保存: {out}')


if __name__ == '__main__':
    analyze()
