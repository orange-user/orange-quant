# 修复橙卫AI路由断点 + 模拟盘去重

## PROJECT_ROOT
C:\Users\Administrator\Desktop\quant_pulse

## 上下文
橙卫AI系统已经训练好XGBoost模型（data/alpha_model.pkl, 60.8%准确率），
scripts/daily_og.py 刚刚被创建但app.py中两个路由引用已损坏的函数：
- 访问 /og/signals → 500（import scripts.daily_og.fetch_signals 函数名不存在）
- 访问 /api/alpha/signals → 500（import alpha_factory.fetch_zt_pool 函数不存在）

另外 data/og_sim_log.jsonl 有9条pending记录，其中3组重复需要去重。

## 参考实现
- scripts/daily_og.py（刚创建）有 load_model, fetch_lhb_data, build_features, filter_today_signals
- alpha_factory.py 有 fetch_lhb（单参数: days）, build_features（单参数: lhb_df）, predict_today（双参数: model_info, features_df）
- lhb_strategy.py 有 get_today_signals, save_daily_signals

## 涉及文件
1. app.py — 2处路由替换
2. data/og_sim_log.jsonl — 去重

## 改动指令

### 改动1：app.py /og/signals 路由
替换约第2425-2434行，原代码：
```python
@app.route('/og/signals')
def og_signals():
    from scripts.daily_og import fetch_signals, load_model
    model_data = load_model()
    signals = fetch_signals(model_data)
    if signals:
        lines = [f"{s['code']} {s['name']} {s['proba']:.0%} 净买{s['nb_ratio']}%" for s in signals]
        return jsonify({'signals': signals, 'text': chr(10).join(lines)})
    return jsonify({'signals': [], 'text': '今日无信号'})
```
替换为：
```python
@app.route('/og/signals')
def og_signals():
    from scripts.daily_og import load_model, fetch_lhb_data, build_features, filter_today_signals
    model_data = load_model()
    if model_data is None:
        return jsonify({'signals': [], 'text': '模型未训练'})
    lhb = fetch_lhb_data(10)
    if lhb is None:
        return jsonify({'signals': [], 'text': '数据获取失败'})
    features = build_features(lhb)
    signals = filter_today_signals(features, model_data)
    if signals:
        lines = [f"{s['code']} {s['name']} {s['proba']:.0%} 净买{s['nb_ratio']}%" for s in signals]
        return jsonify({'signals': signals, 'text': chr(10).join(lines)})
    return jsonify({'signals': [], 'text': '今日无信号'})
```

### 改动2：app.py /api/alpha/signals 路由
替换约第2344-2360行，原代码：
```python
@app.route('/api/alpha/signals')
def api_alpha_signals():
    import pickle, os
    from config import DATA_DIR
    model_path = os.path.join(DATA_DIR, 'alpha_model.pkl')
    if os.path.exists(model_path):
        with open(model_path, 'rb') as f:
            model_data = pickle.load(f)
        from alpha_factory import fetch_lhb, fetch_zt_pool, build_features, predict_today
        lhb = fetch_lhb(10)
        zt = fetch_zt_pool()
        features = build_features(lhb, zt)
        signals = predict_today(model_data, features)
        return jsonify({'signals': signals, 'count': len(signals), 'accuracy': model_data.get('accuracy', 0)})
    return jsonify({'signals': [], 'count': 0, 'error': '模型未训练'})
```
替换为（移除 fetch_zt_pool，修复 build_features 调用）：
```python
@app.route('/api/alpha/signals')
def api_alpha_signals():
    import pickle, os
    from config import DATA_DIR
    model_path = os.path.join(DATA_DIR, 'alpha_model.pkl')
    if os.path.exists(model_path):
        with open(model_path, 'rb') as f:
            model_data = pickle.load(f)
        from alpha_factory import fetch_lhb, build_features, predict_today
        lhb = fetch_lhb(10)
        if lhb is None or len(lhb) == 0:
            return jsonify({'signals': [], 'count': 0, 'error': '数据获取失败'})
        features = build_features(lhb)
        if features is None or len(features) == 0:
            return jsonify({'signals': [], 'count': 0, 'error': '特征构建失败'})
        signals = predict_today(model_data, features)
        return jsonify({'signals': signals, 'count': len(signals), 'accuracy': model_data.get('accuracy', 0)})
    return jsonify({'signals': [], 'count': 0, 'error': '模型未训练'})
```

### 改动3：模拟盘去重
读取 data/og_sim_log.jsonl，按 (code, date) 去重保留第一条。有3组重复：
- (603206, 20260608) 重复3次
- (600198, 20260603) 重复3次
- (601798, 20260603) 重复2次

```python
import json
seen = set()
lines = []
with open('data/og_sim_log.jsonl', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        d = json.loads(line)
        key = (d['code'], d['date'])
        if key not in seen:
            seen.add(key)
            lines.append(line)
with open('data/og_sim_log.jsonl', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')
```

## 完成判据
1. `python3 -c "from app import app; print('OK')"` 输出OK不报错
2. 模拟盘行数从9降为4（去重后剩4行不重复）
3. `grep -c "fetch_zt_pool\|fetch_signals" app.py` 返回0

## 不得改动
- 不改 app.py 其他任何路由
- 不改模型文件（alpha_model.pkl）
- 不改 scripts/daily_og.py
- 不改 config.py / data.py / engine.py
