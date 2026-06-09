# 修复信号中 close_price=0.0 问题

## PROJECT_ROOT
C:\Users\Administrator\Desktop\quant_pulse

## 上下文
alpha_factory.py 的 predict_today() 返回的信号中 close_price 和 turnover 都是 0.0，
因为 build_features() 没有把这两个字段传到输出DataFrame中。
predict_today() 用 r.get('close_price', 0) 和 r.get('turnover_rate', 0) 取不到值。

## 参考实现
alpha_factory.py 第61-124行的 build_features() 函数。
alpha_factory.py 第224-262行的 predict_today() 函数。

## 涉及文件
alpha_factory.py — 1处改动

## 改动指令

### 改 build_features：在 features dict 中增加 close_price 和 turnover 字段
第98行附近，在 features 字典中加两行：
```
'turnover': turnover_rate,      # 原始换手率（用于输出信号）
'close_price': close_price,     # 收盘价（用于输出信号）
```

### 改 predict_today：使用正确的字段名
第252行，把 `r.get('turnover_rate', 0)` 改为 `r.get('turnover', 0)`

## 完成判据
```python
from alpha_factory import fetch_lhb, build_features, predict_today
import pickle
with open('data/alpha_model.pkl', 'rb') as f:
    model_data = pickle.load(f)
lhb = fetch_lhb(5)
features = build_features(lhb)
signals = predict_today(model_data, features)
assert all(s['close_price'] > 0 for s in signals), 'close_price still 0'
```

## 不得改动
- 不改 feature_cols（训练好的模型特征不变）
- 不改模型文件
- 不改其他函数
