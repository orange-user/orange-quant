#!/bin/bash
# D1+D2 扫描结果格式化
DIR=/opt/quant_pulse

MSG=$(python3 << 'PYEOF'
import json, os

d1_file = os.path.join("/opt/quant_pulse", "data", "d1_watch_pool.json")
sig_file = os.path.join("/opt/quant_pulse", "data", "signal_alert.json")
sell_file = os.path.join("/opt/quant_pulse", "data", "signal_alert_sell.json")
pos_file = os.path.join("/opt/quant_pulse", "data", "positions.json")

d1 = json.load(open(d1_file)) if os.path.exists(d1_file) else []
sig = json.load(open(sig_file)) if os.path.exists(sig_file) else {}
sell = json.load(open(sell_file)) if os.path.exists(sell_file) else {}
pos = json.load(open(pos_file)) if os.path.exists(pos_file) else []

lines = []

# D2买入信号
if sig.get("code"):
    d1_chg = sig.get("d1_chg", 0)
    lines.append("🚨 D2买入确认")
    lines.append("股票: " + sig["name"] + "(" + sig["code"] + ")")
    lines.append("昨日D1: 涨" + str(round(d1_chg, 1)) + "%")
    lines.append("买入价: " + str(sig["price"]) + "元")
    lines.append("止损: " + str(sig["stop_loss"]) + "元")
    lines.append("目标: " + str(sig["target"]) + "元")
    lines.append("信号: " + sig.get("detail", ""))
    lines.append("")

# 卖出信号
sell_list = sell.get("sells", []) if isinstance(sell, dict) else (sell if isinstance(sell, list) else [])
if sell_list:
    lines.append("📉 卖出信号 (" + str(len(sell_list)) + "笔)")
    for s in sell_list[-3:]:
        lines.append("  " + s["name"] + "(" + s["code"] + ") " + s["reason"] + " 盈亏" + format(s["pnl_pct"], "+.2f") + "%")
    lines.append("")

# D1观察池TOP5
if d1:
    n = min(5, len(d1))
    lines.append("📊 D1观察池 TOP" + str(n))
    for s in d1[:5]:
        chg = abs(s.get("d1_chg", 5))
        if chg >= 10:
            sl, tp = "-7%", "+7%"
        elif chg >= 8:
            sl, tp = "-5%", "+5%"
        elif chg >= 6:
            sl, tp = "-3%", "+4%"
        else:
            sl, tp = "-2%", "+3%"
        lines.append("  " + s["code"] + " " + str(s["score"]) + "分 D1涨" + format(s["d1_chg"], "+.1f") + "% 量比" + str(s["d1_volume_ratio"]) + " | 止损" + sl + " 止盈" + tp)
    lines.append("")

# 持仓
if pos:
    lines.append("💼 持仓 " + str(len(pos)) + "只")
    for p in pos:
        lines.append("  " + p.get("name", "?") + "(" + p["code"] + ") 买入" + str(p.get("buy_price", "?")) + " " + str(p.get("shares", 0)) + "股")

out = "\n".join(lines).strip()
print(out if out else "今日无符合条件的信号")
PYEOF
)

echo "$MSG"
