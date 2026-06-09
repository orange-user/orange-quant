---
description: Pulse Orange — A股实时形态监控+买点触发量化交易系统
globs: **/*.py, **/*.html, **/*.js, **/*.css, **/*.sh
---

# Pulse Orange

A股超短线量化交易系统，策略：**D1放量大阳 → 观察池 → D2低开回踩确认买入 → 动态止盈止损 + 2日强制卖出**。

> ⚠️ 所有旧策略（14:30尾盘买入、4个买点条件触发[W底/龙头首阴/N字回调/放量突破]）已废弃。全量3177只回测证明全部亏钱或无法触发。

## 架构

- **本地研发**：Flask后端 + Vue3前端（Vue CDN加载，无bundler）+ ECharts图表
- **服务器运行时**：monitor.py 守护进程 + morning_scan.py cron + Flask API
- **数据源**：akshare + Sina分钟K线API + 腾讯批量行情API (`qt.gtimg.cn`)
- **存储**：SQLite（日线缓存）+ JSON文件（持仓/交易/信号/候选池/概念映射）
- **AI**：DeepSeek Chat API（可选，仅影响AI委员会决策显示，非必需）
- **生产运行**：Gunicorn + Nginx 反向代理（:8000 → 80）on 47.114.114.222

## 核心策略：D1+D2

### D1观察池生成（收盘 15:00 cron → `scripts/evening_scan.py`）
全市场扫描 `scan_d1_candidates()` 检测"放量大阳"条件：
- 涨幅 > 4%
- 阳线（close > open）
- 量比 > 1.2
- 强势收盘（收盘在K线上部）
- → 写入 `d1_watch_pool.json`

### D2买入确认（盘中 09:25-15:00 → `server/monitor.py` 每10秒轮询）
对D1观察池检测腾讯API，买入条件：
1. 大盘涨 > 0（否则不开仓）
2. D2低开回踩D1开盘价附近
3. 15分钟K线确认反包
4. 过滤：排除D2开盘跳空大涨、D2已经高位

### 卖出规则
- **动态止盈止损**（基于D1涨幅）：
  - D1涨<6% → 止损-2% 止盈+3%
  - D1涨6-8% → 止损-3% 止盈+4%
  - D1涨8-10% → 止损-5% 止盈+5%
  - D1涨10%+ → 止损-7% 止盈+7%
- **2日强制卖出**：T日买入 → 最晚T+2强制出局

## Windows 本地开发

项目路径：C:\Users\Administrator\Desktop\pulse_orange

启动开发服务器：
```
cd C:\Users\Administrator\Desktop\pulse_orange
python app.py
```

### 项目文件结构
| 文件 | 说明 |
|------|------|
| app.py | Flask路由、/api/analyze、/api/deploy、后台扫描线程、调度器 |
| engine.py | 评分引擎（8个策略因子、五维置信度、技术指标、MTF合成）|
| data.py | 数据获取（akshare/腾讯/Sina）、SQLite缓存、分钟K线 |
| config.py | API keys、FACTOR_REGISTRY（22活跃因子 + DYNAMIC_FACTORS） |
| _pattern_recognition.py | 形态识别引擎（W底/头肩底/箱体/N字/多方炮/单阳不破/MACD背离）|
| _sector_heat.py | 板块热度计算 |
| server/monitor.py | 实时监控守护进程（每10秒轮询候选池+买点检测） |
| scripts/morning_scan.py | 早盘候选池生成（cron 9:25调用） |
| ai_service.py | AI委员会决策、DeepSeek调用 |
| wechat_bot.py | 微信机器人 |
| backtest/ | 回测框架（runner/data_provider/sell_simulator/position_manager） |
| _factor_mining_v2.py | DEAP GP因子挖掘 |

## 生产部署（服务器）

- **IP**: 47.114.114.222（Ubuntu 24.04, Python 3.12）
- **SSH**: root@47.114.114.222（密钥登录）
- **服务**: systemd quant_pulse.service（Nginx → Gunicorn :8000）
- **路径**: `/opt/quant_pulse/`
- **监控**: `server/monitor.py`（nohup后台守护进程）

### 部署更新
```
# 方式1：HTTP部署接口（推荐，通过GFW不依赖SSH）
curl -X POST http://47.114.114.222:8000/api/deploy \
  -F "token=po2024" \
  -F "archive=@deploy.tar.gz"

# 方式2：SSH + SCP
tar czf deploy.tar.gz app.py engine.py data.py config.py ai_service.py \
  server/monitor.py scripts/morning_scan.py _pattern_recognition.py \
  _sector_heat.py templates/ static/ requirements.txt
scp deploy.tar.gz root@47.114.114.222:/opt/quant_pulse/
ssh root@47.114.114.222 "cd /opt/quant_pulse && tar xzf deploy.tar.gz && systemctl restart quant_pulse"
```

## 核心评分逻辑（`engine.py:calculate_comprehensive_score`）

### 五维置信度推荐
- STRONG_BUY = 全部5维满足（红色）
- BUY = >=3维满足且有技术形态（橙色）
- WATCH = 有技术形态但不足（灰色）
- WEAK = 无有效形态（淡）

5个维度：
1. has_pattern — 有技术形态（dragon_rising>=25 或 oversold>=20 或 triple>=15）
2. is_safe — 风险可控（RSI<65, BP<0.85, 5日动量<10%）
3. has_independence — 独立行情（低大盘相关性 r<0.4 或量比>1.2）
4. close_strong — 收盘强势（close_position>0.6）
5. has_volume_trend — 量能配合（量比>1.0）

### 评分流程
1. `_get_pool_snapshot()` → 全市场股票（30s缓存）
2. 过滤ST/退市/市值<50亿/价格<3/涨跌幅>9.5%
3. 按涨跌幅绝对值排序取前300 → `quick_prescreen` → 取前40
4. `calculate_comprehensive_score` 全量评分（含8个策略 + T+1预测因子 + 多时间框架）→ 取前15
5. MTF修正（VWAP/RSI/MACD/尾盘稳定性在60分/15分/5分K线级别验证）
6. 可选AI委员会决策（仅前5名）

### 8个活跃策略
| 策略key | 最大分 | 说明 |
|---------|--------|------|
| dragon_rising | 42 | 潜龙出海 - 放量突破所有均线 |
| oversold_reversal | 34 | 超跌反转 - RSI超卖+底背离+KDJ低位 |
| golden_cross_triple | 25 | 三金叉共振 - MA+MACD+KDJ同步金叉 |
| consecutive_yang | 25 | 连阳蓄势 - 5连阳以上 |
| volume_price | 25 | 量价齐升 - 温和放量上涨 |
| boll_squeeze | 20 | 布林收口 - 带宽收窄后放量突破 |
| momentum_breakout | 15 | 动量突破 - 创N日新高 |
| low_vol_breakout | 15 | 低波突破 - 横盘后放量突破 |

### 形态识别（`_pattern_recognition.py`）
| 形态 | 函数 | 完成 |
|------|------|:----:|
| W底 | detect_w_bottom | ✅ |
| 头肩底 | detect_head_shoulders | ✅ |
| 箱体突破 | detect_box_breakout | ✅ |
| 多方炮 | detect_bull_cannon | ✅ |
| MACD底背离 | detect_macd_divergence | ✅ |
| N字形态 | detect_n_pattern | ❌ 未考证，已移除 |
| 单阳不破 | detect_single_yang | ❌ 未考证，已移除 |

## 自动化定时任务（云服务器）
| 时间 | 任务 | 说明 |
|------|------|------|
| 工作日 15:00 | D1扫描 | `scripts/evening_scan.py` → 写入`d1_watch_pool.json` |
| 工作日 09:25-15:00 | D2监控 | `server/monitor.py` 每10秒轮询D1观察池报价 |
| 任意时间 | 买入信号 | monitor检测D2确认→写入`signal_alert.json`→推微信 |
| 任意时间 | 卖出信号 | monitor检测止盈/止损/2日强制→写入`signal_sell.json`→推微信 |

## 关键设计原则（勿违反）
1. 分数不用简单乘法归一化 - 不要用 * 1.5
2. 不要付费数据源 - akshare + 腾讯 + Sina 够用
3. 颜色由recommendation字段决定，不是分数
4. 手机端Vue/ECharts必须本地加载 - CDN可能被墙
5. 替用户做技术决策，简洁直接
6. MTF信号只做扣分/加分修正，不改原始策略评分
7. HTTP部署优先于SSH部署（过墙友好）

## 项目文件说明
- `.claude/` — cc 技能、配置、代理、CLAUDE.md
- `memory/` — 跨会话持久记忆（cc自动管理）
- `data/` — SQLite数据库、JSON持仓/交易/信号/概念映射
- `backtest/` — 回测框架（含126笔交易分析结论）
- `server/` — 服务器部署脚本（monitor.py守护进程）
- `scripts/` — 定时任务脚本（morning_scan）

## cc 协作规范

### 与小橘子（Hermes Agent）的关系
- 小橘子运行在云服务器上，通过 SSH 反向隧道（端口2222）访问本机
- cc（Claude Code）跑在 Windows 本地
- 小橘子派活 → cc执行 → 小橘子汇总结果推微信
- 隧道由本机主动发起（ssh -R 2222:localhost:22）
- 电脑关机后隧道断开，cc离线

### 开发规范
- 替用户做技术决策，不要问太多问题
- 不需要的功能直接说不
- 给出1-2句推荐，不写长篇分析
