#!/usr/bin/env python3
"""
橙卫AI 每日信号管线

流程:
  1. 拉取龙虎榜数据 (akshare)
  2. 加载XGBoost模型
  3. 生成今日信号 (概率 > 0.6 且 净买额 > 0)
  4. 记录模拟盘 (写入 og_sim_log.jsonl)
  5. 推送到微信 (OpenClaw)

用法:
  python scripts/daily_og.py               # 只生成信号+记录
  python scripts/daily_og.py --push        # 生成信号+推送微信
  python scripts/daily_og.py --train       # 重新训练模型
  python scripts/daily_og.py --backtest    # 回测验证
"""
import sys, os, json, pickle, argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR, logger


# ── 常量 ──
MODEL_PATH = os.path.join(DATA_DIR, 'alpha_model.pkl')
SIM_LOG_PATH = os.path.join(DATA_DIR, 'og_sim_log.jsonl')
MIN_PROBA = 0.60      # 最低概率阈值
MAX_SIGNALS = 5        # 每日最多信号数
CAPITAL = 10000        # 模拟资金


# ════════════════════════════════════════════════
# 模型加载
# ════════════════════════════════════════════════

def load_model():
    """加载已训练的模型"""
    if not os.path.exists(MODEL_PATH):
        logger.error(f"模型文件不存在: {MODEL_PATH}")
        return None
    with open(MODEL_PATH, 'rb') as f:
        data = pickle.load(f)
    return data


# ════════════════════════════════════════════════
# 数据获取
# ════════════════════════════════════════════════

def fetch_lhb_data(days=10):
    """拉取龙虎榜数据"""
    import akshare as ak
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    try:
        df = ak.stock_lhb_detail_em(start_date=start, end_date=end)
        if df is None or len(df) == 0:
            return None
        for c in ['龙虎榜净买额', '净买额占总成交比', '换手率', '涨跌幅', '收盘价',
                   '龙虎榜买入额', '龙虎榜卖出额']:
            if c in df.columns:
                df[c] = df[c].astype(float)
        return df
    except Exception as e:
        logger.error(f"龙虎榜数据拉取失败: {e}")
        return None


def build_features(lhb_df):
    """构建特征矩阵（与训练时一致）"""
    if lhb_df is None or len(lhb_df) == 0:
        return None

    rows = []
    for _, row in lhb_df.iterrows():
        code = str(row['代码']).zfill(6)
        name = str(row['名称'])

        # 排除ST/退市/B股/科创板
        if any(x in name for x in ['ST', '退市', 'B股']):
            continue
        if code.startswith(('8', '688', '900', '200', '300', '920', '11', '12', '123')):
            continue

        nb_ratio = float(row.get('净买额占总成交比', 0))
        net_buy = float(row.get('龙虎榜净买额', 0))
        buy_amt = float(row.get('龙虎榜买入额', 0))
        sell_amt = float(row.get('龙虎榜卖出额', 0))
        turnover = float(row.get('换手率', 0))
        price_chg = float(row.get('涨跌幅', 0))
        close_price = float(row.get('收盘价', 0))

        features = {
            'code': code,
            'name': name,
            'date': str(row['上榜日'])[:10],
            'close_price': close_price,
            'turnover': turnover,
            'price_change': price_chg,
            # 8维特征（匹配XGBoost模型）
            'nb_ratio': nb_ratio,
            'nb_ratio_sq': nb_ratio ** 2,
            'nb_ratio_sign': 1 if nb_ratio > 0 else -1,
            'nb_amt_log': np.log1p(abs(net_buy)) * (1 if net_buy >= 0 else -1),
            'bs_ratio': buy_amt / max(sell_amt, 1),
            'turnover_log': np.log1p(turnover),
            'price_change': price_chg,
            'pc_sign': 1 if price_chg > 0 else -1,
        }
        rows.append(features)

    return pd.DataFrame(rows) if rows else None


def filter_today_signals(features_df, model_data):
    """用模型预测今日信号"""
    if features_df is None or model_data is None:
        return []

    clf = model_data['classifier']
    feature_cols = model_data['feature_cols']

    # 取最新一天的记录（今日龙虎榜数据）
    latest_date = features_df['date'].max()
    today = features_df[features_df['date'] == latest_date].copy()

    if len(today) == 0:
        # 没有今日数据，用最近一天
        today = features_df.drop_duplicates(subset=['code'])

    # 构建预测矩阵
    X = today[feature_cols].fillna(0).values
    probas = clf.predict_proba(X)[:, 1]

    today['proba'] = probas
    today = today.sort_values('proba', ascending=False)
    today = today.drop_duplicates(subset=['code'])

    signals = []
    for _, r in today.iterrows():
        if r['proba'] < MIN_PROBA:
            continue
        if r.get('nb_ratio', 0) <= 0:
            continue

        signals.append({
            'code': r['code'],
            'name': r['name'],
            'date': r['date'],
            'proba': round(float(r['proba']), 3),
            'nb_ratio': round(float(r['nb_ratio']), 1),
            'close_price': round(float(r['close_price']), 2),
            'turnover': round(float(r['turnover']), 2),
            'entry_price': None,   # 待填
            'exit_price': None,    # 待填
            'return': None,        # 待填
            'pnl': None,           # 待填
            'status': 'pending',
        })

    return signals[:MAX_SIGNALS]


# ════════════════════════════════════════════════
# 信号管理（模拟盘记录）
# ════════════════════════════════════════════════

def load_sim_log():
    """加载模拟盘记录"""
    if not os.path.exists(SIM_LOG_PATH):
        return []
    records = []
    with open(SIM_LOG_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def save_sim_log(records):
    """保存模拟盘记录"""
    with open(SIM_LOG_PATH, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def record_signals(signals):
    """将新信号记录到模拟盘（去重）"""
    existing = load_sim_log()
    existing_codes = {(r['code'], r['date']) for r in existing if r.get('status') == 'pending'}

    new_count = 0
    for s in signals:
        key = (s['code'], s['date'])
        if key not in existing_codes:
            existing.append(s)
            existing_codes.add(key)
            new_count += 1

    if new_count > 0:
        save_sim_log(existing)
    return new_count


def print_stats():
    """打印模拟盘统计"""
    records = load_sim_log()
    pending = [r for r in records if r.get('status') == 'pending']
    completed = [r for r in records if r.get('pnl') is not None]

    wins = [r for r in completed if r['pnl'] > 0]
    total_pnl = sum(r['pnl'] for r in completed) if completed else 0

    print(f"\n{'='*50}")
    print(f"  橙卫AI 模拟盘状态")
    print(f"{'='*50}")
    print(f"  总记录: {len(records)}")
    print(f"  待成交: {len(pending)}")
    print(f"  已完成: {len(completed)}")
    if completed:
        print(f"  胜率: {len(wins)/len(completed)*100:.1f}% ({len(wins)}/{len(completed)})")
        print(f"  总盈亏: {total_pnl:+.2f}")
    print(f"{'='*50}\n")
    return records


# ════════════════════════════════════════════════
# 推送
# ════════════════════════════════════════════════

def push_signals(signals):
    """推送到微信（OpenClaw）"""
    if not signals:
        return "今日无信号"

    text = f"【橙卫AI】{datetime.now().strftime('%m-%d')} 信号\n"
    text += f"{'='*30}\n"
    for s in signals[:5]:
        text += f"{s['code']} {s['name']}\n"
        text += f"  概率: {s['proba']:.0%}  净买占比: {s['nb_ratio']}%\n"
        text += f"  收盘: {s['close_price']}  换手: {s['turnover']}%\n"
    text += f"\n操作: D日收盘信号 → D+1开盘买入 → D+2卖出"

    try:
        import subprocess
        result = subprocess.run([
            'curl', '-s', '-X', 'POST',
            'http://localhost:18789/api/send',
            '-H', 'Authorization: Bearer 823b58a55dddc2b6608b047c71f1453679899b9725e4df18',
            '-H', 'Content-Type: application/json',
            '-d', json.dumps({'channel': 'openclaw-weixin', 'content': text})
        ], capture_output=True, text=True, timeout=10)
        return result.stdout
    except Exception as e:
        logger.error(f"推送失败: {e}")
        return f"推送失败: {e}"


# ════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════

def run_daily(do_push=False):
    """每日运行"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 橙卫AI 每日信号管线启动")

    # 1. 加载模型
    print("[1/4] 加载模型...")
    model_data = load_model()
    if model_data is None:
        print("  FAIL: 模型未训练，先跑 --train")
        return

    print(f"  模型: {type(model_data['classifier']).__name__}, "
          f"准确率: {model_data.get('accuracy', 0)*100:.1f}%, "
          f"训练日期: {model_data.get('trained_date', '未知')}")

    # 2. 获取数据
    print("[2/4] 获取龙虎榜数据...")
    lhb = fetch_lhb_data(10)
    if lhb is None:
        print("  FAIL: 龙虎榜数据获取失败")
        return

    print(f"  龙虎榜记录: {len(lhb)}条")

    # 3. 生成信号
    print("[3/4] 构建特征 + 预测...")
    features = build_features(lhb)
    if features is None or len(features) == 0:
        print("  FAIL: 特征构建失败")
        return

    signals = filter_today_signals(features, model_data)
    print(f"  今日信号: {len(signals)}条")

    for s in signals:
        print(f"    {s['code']} {s['name']} | 概率{s['proba']:.0%} | 净买占比{s['nb_ratio']}%")

    # 4. 记录模拟盘
    print("[4/4] 记录模拟盘...")
    new = record_signals(signals)
    print(f"  新增记录: {new}条")

    # 统计
    print_stats()

    # 推送
    if do_push and signals:
        print("推送微信...")
        result = push_signals(signals)
        print(f"  推送结果: {result[:100] if result else '无响应'}")

    return signals


def build_training_features(lhb_df):
    """构建训练特征矩阵（列名与模型匹配：nb_ratio, nb_ratio_sq等）"""
    if lhb_df is None or len(lhb_df) == 0:
        return None

    rows = []
    for _, row in lhb_df.iterrows():
        code = str(row['代码']).zfill(6)
        name = str(row['名称'])
        if any(x in name for x in ['ST', '退市', 'B股']):
            continue
        if code.startswith(('8', '688', '900', '200', '300', '920', '11', '12', '123')):
            continue

        nb_ratio = float(row.get('净买额占总成交比', 0))
        net_buy = float(row.get('龙虎榜净买额', 0))
        buy_amt = float(row.get('龙虎榜买入额', 0))
        sell_amt = float(row.get('龙虎榜卖出额', 0))
        turnover = float(row.get('换手率', 0))
        price_chg = float(row.get('涨跌幅', 0))
        d1_return = float(row.get('上榜后1日', np.nan))

        rows.append({
            'code': code, 'name': name,
            'date': str(row['上榜日'])[:10],
            'nb_ratio': nb_ratio,
            'nb_ratio_sq': nb_ratio ** 2,
            'nb_ratio_sign': 1 if nb_ratio > 0 else -1,
            'nb_amt_log': np.log1p(abs(net_buy)) * (1 if net_buy >= 0 else -1),
            'bs_ratio': buy_amt / max(sell_amt, 1),
            'turnover_log': np.log1p(turnover),
            'price_change': price_chg,
            'pc_sign': 1 if price_chg > 0 else -1,
            'label_d1': d1_return,
        })

    df = pd.DataFrame(rows)
    if len(df) == 0:
        return None
    df['label_positive'] = df['label_d1'].apply(
        lambda x: 1 if not np.isnan(x) and x > 0.2 else (0 if not np.isnan(x) else np.nan)
    )
    return df


def train_xgboost(features_df):
    """训练XGBoost + GroupKFold 5折验证"""
    import xgboost as xgb
    from sklearn.model_selection import GroupKFold
    from sklearn.metrics import accuracy_score

    df = features_df.dropna(subset=['label_positive']).copy()
    if len(df) < 100:
        return {'error': f'样本不足: {len(df)}'}

    feat_cols = ['nb_ratio', 'nb_ratio_sq', 'nb_ratio_sign', 'nb_amt_log',
                 'bs_ratio', 'turnover_log', 'price_change', 'pc_sign']

    X = df[feat_cols].fillna(0).values
    y = df['label_positive'].values
    groups = df['code'].values

    gkf = GroupKFold(n_splits=5)
    fold_scores = []
    all_y_test, all_y_proba = [], []

    for fold, (trn_idx, tst_idx) in enumerate(gkf.split(X, y, groups)):
        X_trn, X_tst = X[trn_idx], X[tst_idx]
        y_trn, y_tst = y[trn_idx], y[tst_idx]

        model = xgb.XGBClassifier(
            n_estimators=100, learning_rate=0.05, max_depth=3,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42 + fold, eval_metric='logloss'
        )
        model.fit(X_trn, y_trn)
        y_pred = model.predict(X_tst)
        y_proba = model.predict_proba(X_tst)[:, 1]
        fold_scores.append(accuracy_score(y_tst, y_pred))
        all_y_test.extend(y_tst.tolist())
        all_y_proba.extend(y_proba.tolist())

    # 最终模型
    final = xgb.XGBClassifier(
        n_estimators=100, learning_rate=0.05, max_depth=3,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, eval_metric='logloss'
    )
    final.fit(X, y)

    # 概率分桶
    results_df = pd.DataFrame({'actual': all_y_test, 'proba': all_y_proba})
    results_df['bucket'] = pd.cut(results_df['proba'],
        bins=[0, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0],
        labels=['<30%', '30-40%', '40-50%', '50-60%', '60-70%', '>70%'])
    bucket_stats = results_df.groupby('bucket', observed=True)['actual'].agg(['mean', 'count'])
    bucket_stats.columns = ['胜率', '样本量']
    bucket_stats['胜率'] = (bucket_stats['胜率'] * 100).round(1)

    return {
        'accuracy': round(float(np.mean(fold_scores)), 3),
        'accuracy_std': round(float(np.std(fold_scores)), 3),
        'fold_scores': [round(s, 3) for s in fold_scores],
        'n_samples': len(all_y_test),
        'bucket_stats': bucket_stats.to_dict(orient='index'),
        'model': final,
        'feature_cols': feat_cols,
    }


def train_new_model(days=180):
    """重新训练XGBoost模型"""
    print(f"训练XGBoost模型 (数据: 最近{days}天)...")

    lhb = fetch_lhb_data(days)
    if lhb is None or len(lhb) < 50:
        print(f"  龙虎榜数据不足: {len(lhb) if lhb is not None else 0}条")
        return

    print(f"  龙虎榜记录: {len(lhb)}条")

    features = build_training_features(lhb)
    if features is None or len(features) == 0:
        print("  特征构建失败")
        return

    labeled = features.dropna(subset=['label_positive'])
    print(f"  标记样本: {len(labeled)}条")

    result = train_xgboost(features)
    if 'error' in result:
        print(f"  训练失败: {result['error']}")
        return

    m = result
    print(f"\n训练完成!")
    print(f"  准确率: {m['accuracy']*100:.1f}% ± {m['accuracy_std']*100:.1f}%")
    print(f"  各折: {[f'{s*100:.1f}%' for s in m['fold_scores']]}")
    print(f"  样本数: {m['n_samples']}")

    bucket = m.get('bucket_stats', {})
    for b, s in bucket.items():
        print(f"  {b}: 胜率{s['胜率']}% (n={s['样本量']})")

    save_data = {
        'classifier': m['model'],
        'feature_cols': m['feature_cols'],
        'accuracy': m['accuracy'],
        'accuracy_std': m['accuracy_std'],
        'trained_date': datetime.now().strftime('%Y-%m-%d'),
    }
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump(save_data, f)
    print(f"模型已保存: {MODEL_PATH}")

    return save_data



def simulate_past_signals():
    import sqlite3
    from config import DB_PATH
    records = load_sim_log()
    pending = [r for r in records if r.get('status') == 'pending' and r.get('entry_price') is None]
    if not pending:
        print("没有待模拟的信号")
        return
    conn = sqlite3.connect(DB_PATH)
    codes = list(set(r['code'] for r in pending))
    dfs = {}
    for code in codes:
        try:
            df = pd.read_sql(
                "SELECT date, open, close FROM daily_data WHERE code=? AND adjust='qfq' ORDER BY date",
                conn, params=(code,)
            )
            if len(df) > 0:
                df['date_str'] = df['date'].astype(str).str[:10]
                dfs[code] = df
        except:
            pass
    updated = 0
    for r in pending:
        code, sig_date = r['code'], r['date']
        if code not in dfs:
            continue
        df = dfs[code]
        dates = df[df['date_str'] > sig_date]['date_str'].unique()
        if len(dates) == 0:
            continue
        d1_date = sorted(dates)[0]
        d1_row = df[df['date_str'] == d1_date]
        if len(d1_row) == 0:
            continue
        entry_price = float(d1_row.iloc[0]['open'])
        dates2 = df[df['date_str'] > d1_date]['date_str'].unique()
        if len(dates2) == 0:
            continue
        d2_date = sorted(dates2)[0]
        d2_row = df[df['date_str'] == d2_date]
        if len(d2_row) == 0:
            continue
        exit_price = float(d2_row.iloc[0]['open'])
        gross_return = (exit_price / entry_price - 1) * 100
        cost_pct = 0.2 + 0.05
        net_return = gross_return - cost_pct
        shares = 100
        pnl = (exit_price - entry_price) * shares - 5 - 5 - exit_price * shares * 0.0005
        r['entry_price'] = round(entry_price, 2)
        r['exit_price'] = round(exit_price, 2)
        r['return'] = round(net_return, 2)
        r['pnl'] = round(pnl, 2)
        r['status'] = 'completed'
        r['entry_date'] = d1_date
        r['exit_date'] = d2_date
        updated += 1
    conn.close()
    save_sim_log(records)
    completed = [r for r in records if r.get('pnl') is not None]
    wins = [r for r in completed if r['pnl'] > 0]
    total_pnl = sum(r['pnl'] for r in completed)
    print('新增模拟:', updated, '/', len(pending))
    print('总已完成:', len(completed))
    if completed:
        print('胜率:', round(len(wins)/len(completed)*100, 1), '%')
        print('总盈亏:', round(total_pnl, 2))
    return records
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='橙卫AI 每日信号管线')
    parser.add_argument('--push', action='store_true', help='推送到微信')
    parser.add_argument('--train', action='store_true', help='重新训练模型')
    parser.add_argument('--backtest', action='store_true', help='回测验证')
    parser.add_argument('--simulate', action='store_true', help='模拟历史信号成交')
    parser.add_argument('--status', action='store_true', help='查看模拟盘状态')
    parser.add_argument('--days', type=int, default=180, help='训练/回测天数')
    args = parser.parse_args()

    if args.train:
        train_new_model(args.days)
    elif args.backtest:
        from lhb_strategy import verify_backtest
        verify_backtest(args.days)
    elif args.simulate:
        simulate_past_signals()
    elif args.status:
        print_stats()
    else:
        run_daily(do_push=args.push)
