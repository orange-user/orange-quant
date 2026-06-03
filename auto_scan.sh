#!/bin/bash
# D1+D2 扫描推送脚本（替换旧版 /api/analyze 调用）
LOG_DIR=/opt/quant_pulse/data/scans
mkdir -p "$LOG_DIR"
TODAY=$(date +%Y-%m-%d)
TIME_LABEL=$(date +%H:%M)
WEEKDAY=$(date +%u)
if [ "$WEEKDAY" -gt 5 ]; then exit 0; fi

LOG_FILE="$LOG_DIR/daily_${TODAY}.log"
echo "--- ${TIME_LABEL} ---" >> "$LOG_FILE"

MSG=$(python3 << 'PYEOF'
import json, os

D1 = "/opt/quant_pulse/data/d1_watch_pool.json"
SIG = "/opt/quant_pulse/data/signal_alert.json"
SEL = "/opt/quant_pulse/data/signal_alert_sell.json"
POS = "/opt/quant_pulse/data/positions.json"

push_parts = []

# D1观察池
if os.path.exists(D1):
    d1 = json.load(open(D1))
    if d1:
        push_parts.append("📊 D1观察池：" + str(len(d1)) + "只")
        for s in d1[:5]:
            push_parts.append("  " + s["code"] + " " + str(s["score"]) + "分 D1涨" + format(s["d1_chg"], "+.1f") + "% 量比" + str(s["d1_volume_ratio"]))
        push_parts.append("")

# D2买入信号
if os.path.exists(SIG):
    sig = json.load(open(SIG))
    if sig.get("code"):
        push_parts.append("🚨 D2买入信号")
        push_parts.append(sig["name"] + " (" + sig["code"] + ")")
        push_parts.append("买入价" + str(sig["price"]) + "  止损" + str(sig["stop_loss"]) + "  目标" + str(sig["target"]))
        push_parts.append("昨日D1涨" + format(sig.get("d1_chg", 0), ".1f") + "%  时间" + sig.get("time", ""))
        push_parts.append("")

# 卖出信号
if os.path.exists(SEL):
    sell = json.load(open(SEL))
    sell_list = sell.get("sells", []) if isinstance(sell, dict) else (sell if isinstance(sell, list) else [])
    if sell_list:
        push_parts.append("📉 卖出信号")
        for s in sell_list[-3:]:
            push_parts.append("  " + s["name"] + "(" + s["code"] + ") " + s["reason"] + " 盈亏" + format(s["pnl_pct"], "+.2f") + "%")
        push_parts.append("")

# 持仓
if os.path.exists(POS):
    pos = json.load(open(POS))
    if pos:
        push_parts.append("💼 持仓 " + str(len(pos)) + "只")
        for p in pos:
            push_parts.append("  " + p.get("name", "?") + "(" + p["code"] + ") 买入" + str(p.get("buy_price", "?")) + " " + str(p.get("shares", 0)) + "股")

out = "\n".join(push_parts).strip()
print(out if out else "暂无信号")
PYEOF
)

echo "$MSG" >> "$LOG_FILE"

# 更新 planet_post_today.txt（给Hermes用）
echo "$MSG" > /opt/quant_pulse/data/planet_post_today.txt

# PushPlus推送（有实质内容才推）
if [ -n "$MSG" ] && [ "$MSG" != "暂无信号" ]; then
    MSG_SHORT=$(echo "$MSG" | head -30)
    /opt/quant_pulse/venv/bin/python3 -c "
import json, urllib.request
data = {
    'token': 'e0619b073bc6494fbbb866cfbd9b6214',
    'title': '量化扫描 ${TIME_LABEL}',
    'content': '''${MSG_SHORT}'''
}
try:
    req = urllib.request.Request('http://www.pushplus.plus/send',
        data=json.dumps(data).encode(),
        headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(req, timeout=10)
except:
    pass
" 2>/dev/null
fi
