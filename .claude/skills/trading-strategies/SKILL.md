---
name: trading-strategies
description: Pulse Orange 量化系统的17个核心策略和评分机制说明
---

# 脉冲橙量化策略说明书

## 评分架构（五维置信度）

### 决策等级
- **STRONG_BUY** (≥70分) — 红色，5维全部满足
- **BUY** (55-69分) — 橙色，≥3维满足
- **WATCH** (40-54分) — 灰色，有技术形态但不足
- **WEAK** (<40分) — 淡色，弱信号

### 5个评分维度
1. `has_pattern` — 有技术形态（dragon_rising≥25 或 oversold≥20 或 triple≥15）
2. `is_safe` — 风险可控（RSI<65, BP<0.85, 5日动量<10%）
3. `has_independence` — 独立行情（低大盘相关性 r<0.4 或量比>1.2）
4. `close_strong` — 收盘强势（close_position>0.6，在K线上部60%）
5. `has_volume_trend` — 量能配合（量比>1.0）

## 扫描流程
1. `_get_pool_snapshot()` → 全市场股票（30s缓存）
2. 过滤ST/退市/市值<50亿/价格<3/涨跌幅>9.5%
3. 按涨跌幅绝对值排序取前300 → quick_prescreen → 取前40
4. calculate_comprehensive_score 全量评分 → 取前15
5. 可选AI委员会决策（仅前5名）
6. 结果缓存300s（_SCAN_CACHE）

## 17个核心策略

| 策略 | 最大分 | 说明 |
|------|--------|------|
| golden_cross_triple | 42 | 三金叉共振 - MA+MACD+KDJ同步金叉 |
| oversold_reversal | 34 | 超跌反转 - RSI超卖+底背离+KDJ低位 |
| momentum_breakout | 30 | 动量突破 - 创20/60日新高+均线多头 |
| dragon_rising | 20 | 蛟龙出海 - 放量突破所有均线（阳线>3%） |
| bull_trend | 25 | 多头趋势 - ADX>25+均线+MACD |
| hot_topic | 25 | 热点题材 - 板块包含AI/半导体/新能源等 |
| event_driven | 25 | 事件驱动 - 新闻匹配涨停/业绩/中标等 |
| consecutive_yang | 20 | 连阳蓄势 - 5连阳以上 |
| volume_price | 20 | 量价齐升 - 温和放量上涨 |
| boll_squeeze | 20 | 布林突破 - 带宽收窄后放量突破上轨 |
| ma_cross | 20 | 均线金叉 - MA5上穿MA20 |
| growth_quality | 20 | 成长质量 - PE 0-30 + PB 0-3 |
| revaluation | 20 | 预期重估 - 利润/收入含"增长" |
| wave_theory | 20 | 波浪回调 - 价格在0.618附近 |
| chan_theory | 15 | 缠论底分型 - 底分型+突破颈线 |
| mountain_climb | 15 | 上山爬坡 - MA5>MA10>MA20 多头排列 |
| low_vol_breakout | 8 | 低波突破 - 20日振幅<3%+突破MA20 |

## 权重更新
- 每笔交易平仓后归因，按盈亏幅度加权
- 综合权重 = IC权重×0.4 + 实战权重×0.6，[0.3, 3.0]
- 小于3笔交易的因子衰减×0.7

## CtaSignal引擎（新旧混合6:4加权）
- 信号：MACrossSignal, BullTrendSignal, BollChannelSignal, RsiSignal, VolumeSignal, ChanBreakSignal
- 风险规则：KDJOverboughtRisk, BollTopRisk, WeakTrendRisk, LowVolumeRisk, HighCorrelationRisk
