import os
import requests
import json
import time
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
API_URL = "https://api.deepseek.com/v1/chat/completions"

def _deepseek_chat(messages, temperature=0.3, max_tokens=500):
    if not API_KEY:
        return None
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    for attempt in range(2):
        try:
            r = requests.post(API_URL, json=data, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            else:
                print(f"DeepSeek {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"请求失败 (尝试 {attempt+1}/2): {e}")
            if attempt == 0:
                time.sleep(3)
    return None

def ai_market_brief(news_list, top_stocks, market_info=""):
    if not API_KEY:
        return "请配置API Key"
    news_text = "\n".join([f"- {n.get('title', n.get('source', '新闻'))}" for n in (news_list or [])[:10]]) or "暂无新闻"
    stocks_text = "\n".join([f"- {s['code']} {s['name']}: {s['signal']}分" for s in (top_stocks or [])[:5]]) or "暂无信号"
    content = _deepseek_chat([
        {"role": "system", "content": "用简洁中文生成市场简报：1.环境判断 2.板块机会 3.风险提示 4.仓位建议。"},
        {"role": "user", "content": f"新闻：\n{news_text}\n\n信号：\n{stocks_text}"}
    ], max_tokens=600)
    return content or "AI简报暂时无法生成"

def ai_enhanced_analysis(news_list, top_stocks):
    if not API_KEY:
        return {"confidence": 1.0, "summary": "AI未配置", "sectors": []}
    news_text = "\n".join([f"{i+1}. {n.get('title', n.get('source', '新闻'))}" for i, n in enumerate((news_list or [])[:15])]) or "暂无新闻"
    stocks_text = "\n".join([f"{i+1}. {s['code']} {s['name']} 评分{s['signal']}分" for i, s in enumerate((top_stocks or [])[:5])]) or "暂无信号"
    content = _deepseek_chat([
        {"role": "system", "content": "返回JSON: {\"confidence\": 0.8-1.5, \"summary\": \"市场总结(50字)\", \"sectors\": [\"板块1\"]}"},
        {"role": "user", "content": f"新闻：\n{news_text}\n\n信号：\n{stocks_text}"}
    ])
    if content:
        if content.startswith("```"): content = content.split("```")[1].replace("json","").strip()
        try: return json.loads(content)
        except: pass
    return {"confidence": 1.0, "summary": "AI不可用", "sectors": []}

def ai_stock_comment(stock_info):
    if not API_KEY:
        return "AI未配置"
    content = _deepseek_chat([
        {"role": "system", "content": "用一句话（30字内）点评这只股票短线机会。"},
        {"role": "user", "content": f"股票：{stock_info.get('code')} {stock_info.get('name')}，评分{stock_info.get('signal')}，{stock_info.get('priority_reason')}"}
    ], max_tokens=80)
    return content or "暂无点评"

def ai_strategy_diagnosis(trades, diary_entries):
    if not API_KEY:
        return "AI未配置"
    trades_text = "\n".join([f"- {t['code']}: 盈亏{t['profit']}元" for t in (trades or [])[-20:]]) or "无交易"
    diary_text = "\n".join([f"- {d['date']}: {d['text'][:80]}" for d in (diary_entries or [])[-10:]]) or "无复盘"
    content = _deepseek_chat([
        {"role": "system", "content": "给出3条改进建议（每条≤30字）。"},
        {"role": "user", "content": f"交易：\n{trades_text}\n\n复盘：\n{diary_text}"}
    ], max_tokens=300)
    return content or "暂无建议"

def ai_generate_factor_code(sample_data, existing_factors, gene_pool=None, prompt_hint=""):
    if not API_KEY:
        return {"error": "AI未配置"}
    existing_text = "\n".join([f"- {f['name']}: {f['description']}" for f in existing_factors])
    if gene_pool:
        pool_text = "\n".join([f"- {g['name']}: {g['description']}" for g in gene_pool[:20]])
        existing_text += f"\n\n可用基础因子库：\n{pool_text}"
    hint_text = f"\n额外提示：{prompt_hint}" if prompt_hint else ""
    content = _deepseek_chat([
        {"role": "system", "content": "返回JSON: {\"name\": \"因子名\", \"code\": \"def factor_name(df):\\n    ...\\n    return score\", \"description\": \"说明\"}，只返回JSON。"},
        {"role": "user", "content": f"样本：{json.dumps(sample_data, ensure_ascii=False)[:500]}\n现有因子：{existing_text}{hint_text}"}
    ], temperature=0.6, max_tokens=600)
    if content:
        if content.startswith("```"): content = content.split("```")[1].replace("json","").strip()
        try: return json.loads(content)
        except: return {"error": "解析失败"}
    return {"error": "AI未返回有效结果"}

def ai_factor_discovery(sample_data, prompt_hint=""):
    """组合生成新因子"""
    if not API_KEY:
        return {"error": "AI未配置"}
    prompt = f"样本数据：{json.dumps(sample_data, ensure_ascii=False)[:800]}\n{prompt_hint}"
    content = _deepseek_chat([
        {"role": "system", "content": "返回JSON: {\"name\": \"因子名\", \"formula\": \"表达式\", \"base_factors\": [\"依赖因子1\"], \"description\": \"说明\"}，只返回JSON。"},
        {"role": "user", "content": prompt}
    ], temperature=0.6, max_tokens=600)
    if content:
        if content.startswith("```"): content = content.split("```")[1].replace("json","").strip()
        try: return json.loads(content)
        except: return {"error": "解析失败"}
    return {"error": "AI未返回有效结果"}


# ==================== AI 委员会（本地分析 + AI增强） ====================

def technical_analyst_local(stock_data):
    score = 50
    reasons = []
    rsi = stock_data.get('rsi', 50)
    adx = stock_data.get('adx', 20)
    change_5d = stock_data.get('change_5d', 0)
    if 30 <= rsi <= 50:
        score += 15; reasons.append("RSI健康")
    elif rsi < 30:
        score += 10; reasons.append("RSI超卖")
    if adx and adx > 25:
        score += 10; reasons.append("趋势明确")
    if 1 < change_5d < 5:
        score += 10; reasons.append("温和上涨")
    return {"score": min(100, max(0, score)), "reason": " + ".join(reasons) if reasons else "技术指标综合评估"}


def fundamental_analyst_local(stock_data, news_list):
    score = 50
    bull_words = ['利好', '增长', '突破', '中标', '合作', '政策']
    bear_words = ['下跌', '风险', '亏损', '处罚', '制裁']
    bull_count = bear_count = 0
    for news in (news_list or [])[:5]:
        title = news.get('title', '')
        if any(w in title for w in bull_words): bull_count += 1
        if any(w in title for w in bear_words): bear_count += 1
    if bull_count > bear_count: score += 15
    elif bear_count > bull_count: score -= 10
    pe = stock_data.get('pe', 20)
    if pe and 0 < pe < 30: score += 10
    return {"score": min(100, max(0, score)), "reason": "消息面综合评估"}


def risk_controller_local(stock_data, position_info):
    score = 70
    risk_level = "低"
    amplitude = stock_data.get('amplitude', 3)
    if amplitude > 5: score -= 15; risk_level = "高"
    elif amplitude > 3: score -= 5; risk_level = "中"
    if len(position_info) >= 3: score -= 10; risk_level = "高"
    max_position = 30 if score > 70 else (20 if score > 50 else 10)
    return {"score": min(100, max(0, score)), "risk_level": risk_level, "max_position": max_position}


def ai_committee_decision(stock_data, news_list, position_info):
    results = {
        "技术分析": technical_analyst_local(stock_data),
        "基本面分析": fundamental_analyst_local(stock_data, news_list),
        "风控分析": risk_controller_local(stock_data, position_info)
    }

    if API_KEY:
        try:
            prompt = f"股票数据：{json.dumps(stock_data, ensure_ascii=False)}\n新闻：{news_list[:3]}\n持仓：{len(position_info)}只"
            ai_tech = _deepseek_chat([
                {"role": "system", "content": "返回JSON: {\"score\":0-100,\"reason\":\"理由\"}"},
                {"role": "user", "content": prompt}
            ], temperature=0.1, max_tokens=100)
            if ai_tech:
                if ai_tech.startswith("```"): ai_tech = ai_tech.split("```")[1].replace("json","").strip()
                ad = json.loads(ai_tech)
                results["技术分析"]["score"] = int(results["技术分析"]["score"] * 0.6 + ad.get("score", 50) * 0.4)
                if ad.get("reason"): results["技术分析"]["reason"] += " | AI: " + ad["reason"]
        except: pass

    tech = results["技术分析"]["score"]
    fund = results["基本面分析"]["score"]
    risk = results["风控分析"]["score"]
    final_decision = "BUY" if (tech >= 60 and fund >= 60 and risk >= 50) else "PASS"
    position_pct = min(30, int((tech + fund) / 2 * (risk / 100)))
    return {
        "decision": final_decision,
        "position_pct": position_pct,
        "technical_score": tech,
        "fundamental_score": fund,
        "risk_score": risk,
        "technical_reason": results["技术分析"]["reason"],
        "fundamental_reason": results["基本面分析"]["reason"],
        "risk_level": results["风控分析"]["risk_level"],
        "summary": f"技术{tech}分 + 基本面{fund}分 + 风控{risk}分 → {'买入' if final_decision=='BUY' else '不买'}"
    }
