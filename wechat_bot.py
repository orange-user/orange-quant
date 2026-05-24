"""
WeChat Official Account Test Account bot for 橘子量化.
Routes commands to real quant APIs, sends context to DeepSeek for responses.
"""
import hashlib
import json
import time
import xml.etree.ElementTree as ET
import urllib.request
import os

WECHAT_TOKEN = os.environ.get("WECHAT_TOKEN", "quant_pulse_2024")
DEEPSEEK_API_KEY = os.environ.get("ANTHROPIC_AUTH_TOKEN", "") or "sk-6c518b55bcbe44bba5d58b8b416f9f8f"
DEEPSEEK_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "") or "https://api.deepseek.com"
LOCAL_API = "http://127.0.0.1:8000"

SYSTEM_PROMPT = """你是橘子量化（Orange Quant）的AI助手，运行在阿里云服务器上。
回复风格：简洁、直接、量化。
重要规则：你必须只使用下面提供的实时数据来回答问题。绝对不要使用你自己的训练数据中的任何市场数据（如指数点位、股票价格等）。如果提供的数据中没有相关信息，就说"暂无该数据"而不是编造。"""


def _get_index_data():
    """Fetch real-time market index data from Sina finance."""
    try:
        url = "http://hq.sinajs.cn/list=sh000001,sz399001,sz399006"
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
        result = {}
        for line in raw.strip().split(";\n"):
            if not line.strip():
                continue
            parts = line.split('="')
            if len(parts) != 2:
                continue
            code = parts[0].replace("var hq_str_", "")
            values = parts[1].strip('";').split(",")
            # Sina index format: [0]name [1]current [2]yesterday_close [3]open [4]high [5]low ...
            if len(values) >= 3:
                price = float(values[1]) if values[1] else 0
                yesterday = float(values[2]) if values[2] else 0
                change = price - yesterday if yesterday else 0
                change_pct = round((change / yesterday) * 100, 2) if yesterday else 0
                sign = "+" if change >= 0 else ""
                result[code] = {
                    "name": values[0],
                    "price": price,
                    "yesterday": yesterday,
                    "change": round(change, 2),
                    "change_pct": change_pct,
                    "display": f"{price:.0f}点 | {sign}{change_pct}%",
                }
        return result
    except Exception:
        return {}


def verify_signature(signature, timestamp, nonce):
    tmp = sorted([WECHAT_TOKEN, timestamp, nonce])
    return hashlib.sha1("".join(tmp).encode()).hexdigest() == signature


def parse_message(xml_data):
    try:
        root = ET.fromstring(xml_data)
        return {child.tag: child.text or "" for child in root}
    except Exception:
        return {}


def build_text_reply(from_user, to_user, content):
    return f"""<xml>
<ToUserName><![CDATA[{from_user}]]></ToUserName>
<FromUserName><![CDATA[{to_user}]]></FromUserName>
<CreateTime>{int(time.time())}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
</xml>"""


def _call_api(endpoint, method="GET", body=None):
    """Call local quant API and return JSON."""
    try:
        url = f"{LOCAL_API}{endpoint}"
        data = None
        if body:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data,
            headers={"Content-Type": "application/json"} if data else {})
        if method == "POST":
            req.method = "POST"
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)[:200]}


def _get_real_data(content_lower):
    """Fetch real quant data based on user's intent. Returns context string."""
    parts = []

    # Always include market indices
    indices = _get_index_data()
    if indices:
        parts.append("[实时大盘 - 务必使用此数据]")
        for code, info in indices.items():
            parts.append(f"  {info['name']}({code}): {info['display']}")
        parts.append("")

    # Market scan
    if any(w in content_lower for w in ["扫描", "扫", "scan", "分析市场", "选股", "荐股", "推荐"]):
        data = _call_api("/api/analyze", "POST")
        if data and "stocks" in data and "error" not in data:
            stocks = data.get("stocks", [])
            total = data.get("total", 0)
            high = sorted([s for s in stocks if s.get("signal", 0) >= 60],
                          key=lambda x: x.get("signal", 0), reverse=True)
            parts.append(f"[实时扫描数据 - {total}只股票]")
            if high:
                parts.append("高分股票:")
                for s in high[:10]:
                    parts.append(
                        f"  {s.get('code','?')} {s.get('name','?')} | "
                        f"信号:{s.get('signal',0)} | 评分:{s.get('score',0):.1f} | "
                        f"现价:{s.get('price','?')}"
                    )
            else:
                parts.append("无高分信号")
        elif "status" in data:
            parts.append(f"[扫描状态: {data.get('status', '未知')}] {data.get('message', '')}")

    # Positions
    if any(w in content_lower for w in ["持仓", "仓位", "position", "持有", "盈亏", "账户"]):
        stats = _call_api("/api/stats")
        if stats and "error" not in stats:
            parts.append(f"\n[持仓数据]")
            parts.append(f"总交易: {stats.get('total_trades',0)}笔 | 胜率: {stats.get('win_rate',0)}%")
            parts.append(f"累计盈亏: {stats.get('total_profit',0)}元 | 浮动盈亏: {stats.get('floating_pnl',0)}元")
            pos = stats.get("positions", [])
            if pos:
                parts.append("当前持仓:")
                for p in pos:
                    parts.append(
                        f"  {p.get('code','?')} {p.get('name','?')} | "
                        f"成本:{p.get('buy_price',0)} | 现价:{p.get('current_price','?')} | "
                        f"盈亏:{p.get('float_profit','?')}%"
                    )
            else:
                parts.append("当前无持仓")
        else:
            parts.append("\n[持仓数据获取失败]")

    # Latest signals from scan logs
    if any(w in content_lower for w in ["信号", "买点", "卖点", "提醒", "alert"]):
        parts.append(f"\n[最新信号]")
        try:
            import glob
            files = sorted(glob.glob("/opt/quant_pulse/data/scans/daily_*.log"), reverse=True)
            if files:
                with open(files[0]) as f:
                    content = f.read()[-2000:]
                    parts.append(content[-1500:])
            else:
                parts.append("暂无扫描记录")
        except Exception:
            parts.append("读取扫描日志失败")

    # Trades
    if any(w in content_lower for w in ["交易", "成交", "trade", "记录", "历史"]):
        trades = _call_api("/api/trades")
        if trades and isinstance(trades, list) and "error" not in trades:
            parts.append(f"\n[最近交易]")
            for t in trades[-5:]:
                parts.append(
                    f"  {t.get('date','?')} {t.get('code','?')} {t.get('name','?')} | "
                    f"盈亏:{t.get('profit',0)} | 收益率:{t.get('return_rate','?')}%"
                )

    return "\n".join(parts) if parts else ""


def chat_with_deepseek(user_message, real_context=""):
    """Send to DeepSeek with real data context."""
    if not DEEPSEEK_API_KEY:
        return "[错误] API Key 未配置"

    full_prompt = user_message
    if real_context:
        full_prompt = f"用户问题: {user_message}\n\n以下是实时系统数据:\n{real_context}\n\n请基于以上数据回答用户的问题。"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": full_prompt},
    ]

    data = json.dumps({
        "model": "deepseek-chat",
        "messages": messages,
        "max_tokens": 500,
        "temperature": 0.3,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{DEEPSEEK_BASE_URL}/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[AI响应失败] {str(e)[:100]}"


def handle_message(xml_data):
    msg = parse_message(xml_data)
    msg_type = msg.get("MsgType", "")
    from_user = msg.get("FromUserName", "")
    to_user = msg.get("ToUserName", "")

    if msg_type == "text":
        content = msg.get("Content", "").strip()
    elif msg_type == "event":
        event = msg.get("Event", "")
        if event == "subscribe":
            return build_text_reply(from_user, to_user,
                "橘子量化 已就绪\n\n"
                "指令:\n"
                "扫描 — 实时市场扫描\n"
                "持仓 — 账户与持仓\n"
                "信号 — 最新买卖信号\n"
                "交易 — 交易记录\n"
                "分析XX — AI分析\n"
                "直接提问也可")
        elif event == "unsubscribe":
            return ""
        else:
            return build_text_reply(from_user, to_user, f"事件: {event}")
    else:
        return ""

    if not content:
        return ""

    # Fetch real data based on content
    real_data = _get_real_data(content.lower())

    # If it's a simple data query with no real data fetched, still try
    if not real_data and any(w in content.lower() for w in ["持仓", "扫描", "信号", "交易"]):
        real_data = _get_real_data("扫描 持仓 信号 交易")

    reply = chat_with_deepseek(content, real_data)

    # Debug logging
    try:
        with open("/opt/quant_pulse/data/wechat_msgs.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            f.write(f"USER: {content}\n")
            if real_data:
                f.write(f"DATA: {real_data[:1000]}\n")
            f.write(f"REPLY: {reply}\n")
    except Exception:
        pass

    return build_text_reply(from_user, to_user, reply)
