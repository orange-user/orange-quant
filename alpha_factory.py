#!/usr/bin/env python3
"""
多源数据Alpha工厂 v2 — 修复版

修复记录：
  v1: 涨停板特征数据泄露（用了今天的涨停板标记历史样本）
      → 移除涨停板特征，仅用龙虎榜数据
  v1: 收盘到收盘标签 vs 实盘开盘到收盘
      → 验证gap avg=+0.13%，加0.2%滑点补偿
  v1: 按时间分割，同股票出现在训练和测试
      → GroupKFold按股票代码分组

数据源：
  1. 龙虎榜 — 净买额占比、买入卖出比、换手率、成交规模

方法论：
  不是规则引擎(IF条件THEN买入)
  而是：特征提取 → 随机森林分类器 → 概率输出
"""
import sys, os, json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR, logger


# ── 参数 ──
SLIPPAGE = 0.002  # 0.2%滑点
TRAIN_DAYS = 365  # 一年数据


# ════════════════════════════════════════════════
# 数据获取
# ════════════════════════════════════════════════

def fetch_lhb(days: int = TRAIN_DAYS) -> pd.DataFrame:
    """获取龙虎榜历史数据（无未来信息）"""
    import akshare as ak
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    try:
        df = ak.stock_lhb_detail_em(start_date=start, end_date=end)
        if df is not None and len(df) > 0:
            for c in ['龙虎榜净买额', '净买额占总成交比', '上榜后1日', '上榜后2日',
                       '换手率', '涨跌幅', '收盘价', '龙虎榜买入额', '龙虎榜卖出额']:
                if c in df.columns:
                    df[c] = df[c].astype(float)
            return df
    except Exception as e:
        logger.error(f"LHB fetch error: {e}")
    return None


# ════════════════════════════════════════════════
# 特征工程（只有龙虎榜数据，无未来函数）
# ════════════════════════════════════════════════

def build_features(lhb_df: pd.DataFrame) -> pd.DataFrame:
    """构建特征矩阵

    每行 = 一只股票一天的数据
    特征都是上榜当日已知信息（无未来数据）
    标签 = 上榜后1日收益 - 滑点

    排除：ST/退市/B股/科创板/北交所/创业板
    """
    if lhb_df is None or len(lhb_df) == 0:
        return None

    rows = []
    for _, row in lhb_df.iterrows():
        code = str(row['代码']).zfill(6)
        name = str(row['名称'])

        # 排除不可交易的股票
        if any(x in name for x in ['ST', '退市', 'B股']):
            continue
        if code.startswith(('8', '688', '900', '200', '300', '920', '11', '12', '123')):
            continue

        # ── 特征（全部来自龙虎榜当日数据，无未来信息）──
        net_buy_ratio = float(row.get('净买额占总成交比', 0))
        net_buy_amount = float(row.get('龙虎榜净买额', 0))
        buy_amount = float(row.get('龙虎榜买入额', 0))
        sell_amount = float(row.get('龙虎榜卖出额', 0))
        turnover_rate = float(row.get('换手率', 0))
        price_change = float(row.get('涨跌幅', 0))
        close_price = float(row.get('收盘价', 0))

        # 衍生特征
        buy_sell_ratio = buy_amount / max(sell_amount, 1)
        net_buy_amount_log = np.log1p(abs(net_buy_amount)) * (1 if net_buy_amount >= 0 else -1)
        turnover_log = np.log1p(turnover_rate)

        features = {
            'code': code,
            'name': name,
            'date': str(row['上榜日'])[:10],

            # 特征
            'net_buy_ratio': net_buy_ratio,
            'net_buy_ratio_sq': net_buy_ratio ** 2,
            'net_buy_amount_log': net_buy_amount_log,
            'buy_sell_ratio': buy_sell_ratio,
            'turnover_log': turnover_log,
            'turnover': turnover_rate,      # 原始换手率
            'close_price': close_price,     # 收盘价

            # 标签：上榜后1日 - 滑点（保守估计）
            'label_d1': float(row.get('上榜后1日', np.nan)),
        }

        # 标签扣除滑点
        d1 = features['label_d1']
        if not np.isnan(d1):
            features['label_positive'] = 1 if d1 > SLIPPAGE * 100 else 0
        else:
            features['label_positive'] = np.nan

        rows.append(features)

    result = pd.DataFrame(rows)
    return result


# ════════════════════════════════════════════════
# 训练（GroupKFold交叉验证）
# ════════════════════════════════════════════════

def train_model(features_df: pd.DataFrame) -> dict:
    """训练随机森林 + GroupKFold交叉验证

    关键：按股票代码分组，确保同一只股票不出现在训练和测试中
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import GroupKFold
    from sklearn.metrics import accuracy_score

    df = features_df.dropna(subset=['label_positive']).copy()
    if len(df) < 100:
        return {'error': f'样本不足: {len(df)}'}

    feature_cols = [
        'net_buy_ratio', 'net_buy_ratio_sq', 'net_buy_amount_log',
        'buy_sell_ratio', 'turnover_log',
    ]
    feature_cols = [c for c in feature_cols if c in df.columns]

    X = df[feature_cols].fillna(0).values
    y = df['label_positive'].values
    groups = df['code'].values  # 按股票代码分组

    # GroupKFold：同一只股票不会同时出现在训练和测试中
    gkf = GroupKFold(n_splits=5)

    fold_scores = []
    all_y_test = []
    all_y_proba = []
    all_feat_imp = []

    for fold, (train_idx, test_idx) in enumerate(gkf.split(X, y, groups)):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model = RandomForestClassifier(
            n_estimators=100, max_depth=5, min_samples_leaf=10,
            class_weight='balanced', random_state=42 + fold
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]

        acc = accuracy_score(y_test, y_pred)
        fold_scores.append(acc)
        all_y_test.extend(y_test.tolist())
        all_y_proba.extend(y_proba.tolist())
        all_feat_imp.append(model.feature_importances_)

    # 完整数据集训练最终模型
    final_model = RandomForestClassifier(
        n_estimators=100, max_depth=5, min_samples_leaf=10,
        class_weight='balanced', random_state=42
    )
    final_model.fit(X, y)

    # 特征重要性（取5折平均）
    avg_importance = np.mean(all_feat_imp, axis=0)
    importance = dict(zip(feature_cols, avg_importance))
    importance = dict(sorted(importance.items(), key=lambda x: -x[1]))

    # 概率分桶
    results_df = pd.DataFrame({
        'actual': all_y_test,
        'predicted_proba': all_y_proba,
    })
    results_df['prob_bucket'] = pd.cut(
        results_df['predicted_proba'],
        bins=[0, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0],
        labels=['<30%', '30-40%', '40-50%', '50-60%', '60-70%', '>70%']
    )
    bucket_stats = results_df.groupby('prob_bucket', observed=True)['actual'].agg(['mean', 'count'])
    bucket_stats.columns = ['胜率', '样本量']
    bucket_stats['胜率'] = (bucket_stats['胜率'] * 100).round(1)

    return {
        'accuracy': round(float(np.mean(fold_scores)), 3),
        'accuracy_std': round(float(np.std(fold_scores)), 3),
        'fold_scores': [round(s, 3) for s in fold_scores],
        'feature_importance': importance,
        'bucket_stats': bucket_stats.to_dict(orient='index'),
        'n_train': len(X),
        'n_samples': len(all_y_test),
        'model': final_model,
        'feature_cols': feature_cols,
    }


# ════════════════════════════════════════════════
# 预测
# ════════════════════════════════════════════════

def predict_today(model_info: dict, features_df: pd.DataFrame) -> List[Dict]:
    """用训练好的模型预测今日最优买入标的"""
    if 'model' not in model_info:
        return []

    model = model_info['model']
    feature_cols = model_info['feature_cols']

    today = features_df.dropna(subset=['net_buy_ratio']).copy()
    if 'date' in today.columns:
        latest_date = today['date'].max()
        today = today[today['date'] == latest_date]

    if len(today) == 0:
        return []

    X = today[feature_cols].fillna(0)
    probas = model.predict_proba(X)[:, 1]

    results = today.copy()
    results['predict_proba'] = probas
    results = results.sort_values('predict_proba', ascending=False)
    results = results.drop_duplicates(subset=['code'])

    signals = []
    for _, r in results.head(10).iterrows():
        if r.get('net_buy_ratio', 0) <= 0:
            continue
        signals.append({
            'code': r['code'],
            'name': r['name'],
            'probability': round(float(r['predict_proba']), 3),
            'net_buy_ratio': round(float(r['net_buy_ratio']), 2),
            'turnover': round(float(r.get('turnover', 0)), 2),
            'close_price': round(float(r.get('close_price', 0)), 2),
            'date': str(r.get('date', '')),
        })

    return signals


# ════════════════════════════════════════════════
# 全流程
# ════════════════════════════════════════════════

def run_pipeline(train_days: int = TRAIN_DAYS) -> dict:
    """运行全流程"""
    print(f"[Alpha] 训练数据: 最近{train_days}天")

    lhb = fetch_lhb(train_days)
    if lhb is None:
        return {'error': '获取龙虎榜数据失败'}

    print(f"[Alpha] 构建特征矩阵 ({len(lhb)}行 → {len(lhb[lhb['代码'].notna()])}行)...")
    features = build_features(lhb)
    if features is None or len(features) == 0:
        return {'error': '特征构建失败'}

    labeled = features.dropna(subset=['label_positive'])
    print(f"[Alpha] 训练随机森林... ({len(labeled)}个标记样本)")
    model_result = train_model(features)

    if 'error' in model_result:
        return model_result

    print(f"[Alpha] 预测今日信号...")
    signals = predict_today(model_result, features)

    return {
        'model': model_result,
        'signals': signals,
        'n_features': len(features),
        'n_lhb': len(lhb),
    }


# ════════════════════════════════════════════════
# 测试入口
# ════════════════════════════════════════════════

if __name__ == '__main__':
    result = run_pipeline()
    if 'error' in result:
        print(f"\nError: {result['error']}")
    else:
        m = result['model']
        print(f"\n{'='*55}")
        print(f"  Alpha工厂 v2 — GroupKFold验证")
        print(f"{'='*55}")
        print(f"  总样本: {m['n_samples']} | 特征数: {len(m['feature_cols'])}")
        print(f"  5折准确率: {m['accuracy']*100:.1f}% ± {m['accuracy_std']*100:.1f}%")
        print(f"  各折: {[f'{s*100:.1f}%' for s in m['fold_scores']]}")

        print(f"\n  特征重要性:")
        for feat, imp in list(m['feature_importance'].items())[:6]:
            print(f"    {feat}: {imp:.1%}")

        print(f"\n  概率分桶（5折汇总）:")
        for bucket, stats in m['bucket_stats'].items():
            print(f"    {bucket}: 胜率{stats['胜率']}% (n={stats['样本量']})")

        signals = result.get('signals', [])
        print(f"\n  今日信号 ({len(signals)}):")
        for s in signals[:6]:
            print(f"    {s['code']} {s['name']} | 概率{s['probability']:.1%} | 净买占比{s['net_buy_ratio']}%")
