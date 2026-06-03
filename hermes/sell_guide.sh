#!/bin/bash
# D1+D2 卖出指导
MSG=$(python3 << 'PYEOF'
import json, os, urllib.request

POS_FILE = "/opt/quant_pulse/data/positions.json"
SELL_FILE = "/opt/quant_pulse/data/signal_alert_sell.json"
D1_FILE = "/opt/quant_pulse/data/d1_watch_pool.json"

if not os.path.exists(POS_FILE):
    print("无持仓，今日无需卖出")
    exit(0)

with open(POS_FILE) as f:
    positions = json.load(f)

if not positions:
    print("无持仓，今日无需卖出")
    exit(0)

# D1涨幅记录
d1_map = {}
if os.path.exists(D1_FILE):
    for s in json.load(open(D1_FILE)):
        d1_map[s["code"]] = s.get("d1_chg", 5)

# 卖出信号记录
sell_list = []
if os.path.exists(SELL_FILE):
    sell_data = json.load(open(SELL_FILE))
    sell_list = sell_data.get("sells", []) if isinstance(sell_data, dict) else (sell_data if isinstance(sell_data, list) else [])

sell_map = {s["code"]: s for s in sell_list}

msg = "💡 卖出指导\n\n"

for pos in positions:
    code = pos["code"]
    buy_price = pos["buy_price"]
    name = pos.get("name", code)

    # 腾讯实时行情
    url = "http://qt.gtimg.cn/q=" + code
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=5)
        text = resp.read().decode("gbk")
        fields = text.split("~")
        if len(fields) > 3 and fields[3]:
            current_price = float(fields[3])
            name = fields[1] if fields[1] else name
        else:
            current_price = buy_price
    except:
        current_price = buy_price

    profit_pct = (current_price - buy_price) / buy_price * 100

    # 动态止盈止损（基于D1涨幅）
    d1_chg = abs(d1_map.get(code, 5))
    if d1_chg >= 10:
        sl_pct, tp_pct = 0.93, 1.07
    elif d1_chg >= 8:
        sl_pct, tp_pct = 0.95, 1.05
    elif d1_chg >= 6:
        sl_pct, tp_pct = 0.97, 1.04
    else:
        sl_pct, tp_pct = 0.98, 1.03

    stop_loss = round(buy_price * sl_pct, 2)
    take_profit = round(buy_price * tp_pct, 2)

    # 是否已卖出
    sold_info = sell_map.get(code)
    if sold_info:
        emoji = "✅" if sold_info["pnl_pct"] > 0 else "🔴"
        msg += emoji + " " + code + " " + name + " — 已卖出\n"
        msg += "   卖出价" + str(sold_info["sell_price"]) + "  盈亏" + format(sold_info["pnl_pct"], "+.2f") + "%  原因:" + sold_info["reason"] + "\n\n"
        continue

    # 建议
    if profit_pct >= (tp_pct - 1) * 100 * 0.8:
        advice = "接近止盈线" + str(take_profit) + "，可考虑分批止盈"
        emoji = "✅"
    elif profit_pct > 0:
        advice = "小幅盈利，止损线" + str(stop_loss) + "，可按计划持有"
        emoji = "✅"
    elif profit_pct > (sl_pct - 1) * 100:
        advice = "小幅亏损，止损线" + str(stop_loss) + "，观察是否企稳"
        emoji = "⚠️"
    else:
        advice = "已跌破止损线" + str(stop_loss) + "，建议及时止损"
        emoji = "🔴"

    msg += emoji + " " + code + " " + name + "\n"
    msg += "   成本" + str(buy_price) + " → 现价" + str(current_price) + " (" + format(profit_pct, "+.2f") + "%)\n"
    msg += "   止损" + str(stop_loss) + "  止盈" + str(take_profit) + "  (基于D1涨" + format(d1_chg, ".0f") + "%)\n"
    msg += "   建议: " + advice + "\n\n"

print(msg.strip())
PYEOF
)

echo "$MSG"
