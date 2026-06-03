---
name: data-sources
description: Pulse Orange 数据源架构和缓存策略
---

# 脉冲橙数据源说明

## 数据获取链路（data.py）

### K线数据 `get_stock_daily_cached(code, days)`
1. **SQLite缓存** — 首次检查，若存在≥days行直接返回
2. **akshare主源** — `ak.stock_zh_a_hist(symbol, 'daily', 'qfq')`，写入SQLite+CSV
3. **CSV缓存** — 保留7天
4. **adata包** — 自定义数据源
5. **腾讯历史API** — `ak.stock_zh_a_hist_tx`
6. **Scrapling** — FetcherSession伪装Chrome直接拉EastMoney（绕过akshare）

### 股票池快照 `_get_pool_snapshot()` (30s缓存)
- Source 1: 腾讯批量行情 (`qt.gtimg.cn`)
- Source 2: 东方财富现货 (`ak.stock_zh_a_spot_em`)
- Source 3: 新浪自定义抓取器
- Source 4: akshare新浪回退

## 缓存架构

**进程内缓存（TTL）：**
- `_pool_cache` — 30秒
- `_info_cache` — 1小时
- `_sector_cache` — 1小时
- `_news_cache` — 300秒
- `_index_cache` — 300秒
- `_SCAN_CACHE` — 300秒（扫描结果）
- `_API_CACHE` — stats 60s, news 120s, ticker 10s, moneyflow 120s, heatmap 120s

**SQLite持久化：**
- `daily_data` 表 — (code, date, open, close, high, low, volume, 联合主键)
- `factor_cache` 表 — (code, date, factor_name, value)
- `init_db()` 在 app.py 启动时调用创建表

## 过滤规则
- ST/退市排除
- 688/689（科创板）排除
- 300/301（创业板）排除
- 市值 < 50亿 排除
- 价格 < 3元 排除
- 涨跌幅 > 9.5% 排除（避免追涨停）

## 已知问题
- akshare在系统日期2026年后部分接口失效
- Scrapling作为最终回退，通过伪装Chrome TLS指纹绕过限制
- 腾讯行情接口最稳定但字段较少
