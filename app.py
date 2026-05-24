import time, datetime, random, json, os, subprocess, threading
import base64
from io import BytesIO
import numpy as np
import pandas as pd
from flask import Flask, render_template, jsonify, request
import akshare as ak
from scipy import stats
from concurrent.futures import ProcessPoolExecutor, as_completed
import warnings

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle, FancyBboxPatch

warnings.filterwarnings('ignore')

from config import *
from data import *
from data import _get_pool_snapshot
from engine import *

# Try loading AI services
try:
    from ai_service import (ai_market_brief, ai_enhanced_analysis, ai_strategy_diagnosis,
                            ai_generate_factor_code, ai_factor_discovery,
                            ai_committee_decision)
    AI_AVAILABLE = True
except Exception as e:
    print(f"AI services not available: {e}")
    AI_AVAILABLE = False

app = Flask(__name__)

# Initialize
init_db()
load_registry()

# ==================== 数据刷新调度器 ====================
_data_refresh_lock = threading.Lock()
_last_refresh_date = None

def _check_and_refresh():
    """每个交易日收盘后（15:30）自动刷新持仓股票的日线数据"""
    global _last_refresh_date
    today = datetime.date.today()
    wd = today.weekday()
    if wd >= 5:
        return
    if _last_refresh_date == today:
        return
    now = datetime.datetime.now()
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        return
    with _data_refresh_lock:
        if _last_refresh_date == today:
            return
        try:
            n = refresh_all_daily_data()
            _last_refresh_date = today
            print(f"[调度] 收盘数据刷新完成：{n} 只股票")
        except Exception as e:
            print(f"[调度] 数据刷新失败: {e}")

def _start_scheduler():
    """后台线程：每5分钟检查一次是否需要刷新数据"""
    def _loop():
        while True:
            try:
                _check_and_refresh()
            except:
                pass
            time.sleep(300)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()

_start_scheduler()

# ==================== WeChat Bot ====================
from wechat_bot import verify_signature, handle_message, WECHAT_TOKEN

@app.route('/wechat', methods=['GET', 'POST'])
def wechat():
    if request.method == 'GET':
        signature = request.args.get('signature', '')
        timestamp = request.args.get('timestamp', '')
        nonce = request.args.get('nonce', '')
        echostr = request.args.get('echostr', '')
        if verify_signature(signature, timestamp, nonce):
            return echostr
        return 'verification failed'
    else:
        xml_data = request.data
        reply = handle_message(xml_data)
        return reply, 200, {'Content-Type': 'application/xml'}

# ==================== PWA Service Worker ====================
@app.route('/sw.js')
def service_worker():
    return app.send_static_file('sw.js'), 200, {'Content-Type': 'application/javascript'}

# ==================== 工具函数 ====================
def calc_stats():
    trades = load_trades(); positions = load_positions()
    total_profit = sum(t.get('profit',0) for t in trades)
    win = [t for t in trades if t.get('profit',0)>0]
    wr = len(win)/len(trades)*100 if trades else 0
    floating_pnl = 0
    for p in positions:
        try:
            df = ak.stock_zh_a_spot_em()
            row = df[df['代码']==p['code']]
            if len(row)>0:
                cp = float(row['最新价'].values[0])
                p['current_price'] = cp
                p['float_profit'] = round((cp-p['buy_price'])/p['buy_price']*100,2)
                floating_pnl += (cp - p['buy_price']) * p.get('shares', 100)
        except: pass
    best_trade = max(trades, key=lambda t: t.get('profit', 0)) if trades else None
    worst_trade = min(trades, key=lambda t: t.get('profit', 0)) if trades else None
    return {'total_trades':len(trades),'win_rate':round(wr,1),'total_profit':round(total_profit,2),
            'floating_pnl':round(floating_pnl,2),'positions':positions,'total_positions':len(positions),
            'best_trade':best_trade,'worst_trade':worst_trade,
            'avg_profit':round(total_profit/len(trades),2) if trades else 0}

def _compute_backtest_metrics(results):
    df_r = pd.DataFrame(results)
    total_trades = len(df_r)
    if total_trades == 0: return None
    win_trades = int(df_r['win'].sum())
    win_rate = win_trades / total_trades * 100
    avg_return = float(df_r['return'].mean())
    cumulative = 1
    cum_returns = []
    for r in df_r['return'].values / 100:
        cumulative *= (1 + r)
        cum_returns.append(round((cumulative - 1) * 100, 2))
    peak = 0; max_dd = 0
    for cum in cum_returns:
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    std_ret = float(df_r['return'].std())
    sharpe = round((avg_return / 100) / (std_ret / 100) * (252 ** 0.5), 2) if std_ret > 0 else 0
    return {
        'total_trades': total_trades, 'win_rate': round(win_rate, 1),
        'avg_return': round(avg_return, 2), 'cum_return': cum_returns[-1] if cum_returns else 0,
        'max_drawdown': round(max_dd, 2), 'sharpe': sharpe,
        'trades': df_r[['date', 'code', 'name', 'signal', 'buy_price', 'sell_price', 'return']].to_dict('records')[:30]
    }

def _simulate_one_day(d, pool_df, top_n):
    results = []
    if pool_df is None or len(pool_df) == 0: return results
    pool_df = pool_df[~pool_df['名称'].str.contains('ST|退')]
    pool_df = pool_df[pool_df['总市值'] > 50e8]
    pool_df = pool_df[pool_df['最新价'] > 3]
    signals = []
    for code in pool_df['代码'].head(100).tolist():
        res = calculate_comprehensive_score(code)
        if res and res['signal'] > 45:
            res['code'] = code
            signals.append(res)
    signals.sort(key=lambda x: x['signal'], reverse=True)
    for s in signals[:top_n]:
        code = s.get('code', '')
        df = get_stock_daily_cached(code, 60)
        if df is None or len(df) < 2: continue
        date_list = df['date'].tolist()
        if d not in date_list: continue
        idx = date_list.index(d)
        if idx + 1 >= len(df): continue
        buy_price = float(df.iloc[idx]['close'])
        sell_price = float(df.iloc[idx + 1]['open'])
        ret = (sell_price - buy_price) / buy_price
        results.append({
            'date': str(d), 'code': code, 'name': s.get('name', ''),
            'signal': s['signal'], 'buy_price': round(buy_price, 2),
            'sell_price': round(sell_price, 2), 'return': round(ret * 100, 2), 'win': ret > 0
        })
    return results

def _check_overfit(in_metrics, out_metrics):
    if not in_metrics or not out_metrics:
        return {'level': 'unknown', 'message': '数据不足，无法判断过拟合程度'}
    in_wr = in_metrics['win_rate']; out_wr = out_metrics['win_rate']
    gap = in_wr - out_wr
    if gap > 20:
        return {'level': 'high', 'message': f'样本内胜率{in_wr}% vs 样本外{out_wr}%%，差值{gap:.0f}%%，存在严重过拟合风险'}
    elif gap > 10:
        return {'level': 'medium', 'message': f'样本内胜率{in_wr}% vs 样本外{out_wr}%%，差值{gap:.0f}%%，存在一定过拟合风险'}
    else:
        return {'level': 'low', 'message': f'样本内胜率{in_wr}% vs 样本外{out_wr}%%，差值{gap:.0f}%%，过拟合风险较低'}

def analyze_news_sectors(title, content):
    text = title+content
    sector_map = {
        '电力':(['电力','电网','能源','发电','特高压','输电','算电协同'],'电力板块'),
        '半导体':(['芯片','半导体','集成电路','光刻','晶圆','算力'],'半导体板块'),
        'AI':(['人工智能','AI','大模型','深度学习'],'AI板块'),
        '新能源车':(['新能源车','电动车','锂电池','充电桩'],'新能源车板块'),
        '银行':(['降准','降息','银行','信贷','利率'],'银行板块'),
        '房地产':(['房地产','房价','楼市','购房'],'房地产板块'),
        '医药':(['医药','医疗','药品','疫苗'],'医药板块'),
        '消费':(['消费','零售','电商','家电','白酒'],'消费板块'),
        '军工':(['军工','国防','武器','军事'],'军工板块'),
        '光伏':(['光伏','太阳能','硅料'],'光伏板块')
    }
    results = []
    for k,(kws,sec) in sector_map.items():
        if any(kw in text for kw in kws): results.append({'sector':sec,'type':'利好'})
    if any(w in text for w in ['下跌','暴跌','崩盘','制裁','关税','加息']):
        for r in results: r['type']='利空'
    return results[:3]

def extract_reminder(text):
    kw = {'止损':'严格执行止损','追高':'避免追高','仓位':'注意仓位','贪婪':'克服贪婪','纪律':'坚守纪律','冲动':'避免冲动','耐心':'保持耐心','回撤':'控制回撤'}
    reminders = [rm for k,rm in kw.items() if k in text]
    return reminders[:3] if reminders else ['坚持复盘']


# ==================== 路由 ====================

@app.route('/')
def index():
    return render_template('index.html')

# 扫描缓存（5分钟内不重复全量扫描）
_SCAN_CACHE = {'time': 0, 'data': None}

@app.route('/api/analyze', methods=['POST'])
def analyze():
    if not is_trading_day():
        wd = get_weekday()
        msg = '周末休市，请在工作日进行扫描' if wd >= 5 else '今日非交易日，数据可能不是最新'
        return jsonify({'status': 'non_trading', 'message': msg, 'stocks': [], 'total': 0})

    # 5分钟内返回缓存
    now = time.time()
    if _SCAN_CACHE['data'] and (now - _SCAN_CACHE['time']) < 300:
        cached = _SCAN_CACHE['data'].copy()
        cached['cached'] = True
        return jsonify(cached)

    pool = _get_pool_snapshot()
    if pool is None or len(pool) == 0:
        if _SCAN_CACHE['data']:
            cached = _SCAN_CACHE['data'].copy()
            cached['cached'] = True
            cached['status'] = 'stale'
            return jsonify(cached)
        return jsonify({'status': 'error', 'message': '行情数据获取失败，请稍后重试（可能为非交易日或网络异常）', 'stocks': [], 'total': 0})
    pool = pool[~pool['名称'].str.contains('ST|退')]
    pool = pool[pool['总市值']>50e8]; pool = pool[pool['最新价']>3]
    codes = pool['代码'].tolist()
    candidates = []
    with ProcessPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(calculate_comprehensive_score, c):c for c in codes}
        for fut in as_completed(futures):
            res = fut.result()
            if res and res['signal']>40:
                row = pool[pool['代码']==res['code']]
                if len(row)>0:
                    r = row.iloc[0]
                    res['name'] = r['名称']; res['price'] = r['最新价']; res['change_pct'] = r['涨跌幅']
                    candidates.append(res)

    # AI委员会自动分析前5只
    if AI_AVAILABLE:
        for i, candidate in enumerate(candidates[:5]):
            try:
                stock_data = {
                    'code': candidate['code'], 'latest_price': candidate['price'],
                    'change_pct': candidate['change_pct'], 'rsi': candidate['rsi'],
                    'adx': candidate['adx'], 'volume_ratio': candidate['volume_ratio'],
                    'momentum_5d': candidate['momentum_5d'],
                    'priority_reason': candidate['priority_reason'],
                    'basic_info': candidate.get('basic_info', {})
                }
                try:
                    news = ak.stock_info_global_em().head(10).to_dict('records')
                except:
                    news = []
                positions = load_positions()
                ai_result = ai_committee_decision(stock_data, news, positions)
                candidate['ai_decision'] = ai_result.get('decision', 'PASS')
                candidate['ai_summary'] = ai_result.get('summary', '')
                candidate['ai_technical_score'] = ai_result.get('technical_score', 50)
                candidate['ai_fundamental_score'] = ai_result.get('fundamental_score', 50)
                candidate['ai_risk_score'] = ai_result.get('risk_score', 50)
                candidate['ai_risk_level'] = ai_result.get('risk_level', '未知')
                candidate['ai_position_pct'] = ai_result.get('position_pct', 10)
                if ai_result.get('decision') == 'BUY':
                    candidate['signal'] = min(100, candidate['signal'] + 10)
                else:
                    candidate['signal'] = max(0, candidate['signal'] - 5)
            except Exception as e:
                candidate['ai_decision'] = 'ERROR'
                candidate['ai_summary'] = f'AI分析失败: {str(e)[:50]}'

    candidates.sort(key=lambda x:x['signal'], reverse=True)
    result = {'status': 'ok', 'stocks': candidates[:15], 'total': len(candidates)}
    _SCAN_CACHE['time'] = time.time()
    _SCAN_CACHE['data'] = {'status': 'ok', 'stocks': candidates[:15], 'total': len(candidates)}
    return jsonify(result)

@app.route('/api/sell_check', methods=['POST'])
def sell_check():
    data = request.get_json()
    code = data.get('code','').strip()
    buy_price = float(data.get('buy_price',0))
    if not code: return jsonify({'error':'缺少代码'}),400
    try:
        cp = chg = name = None
        try:
            spot = ak.stock_zh_a_spot_em()
            row = spot[spot['代码']==code]
            if len(row)>0:
                cp = float(row['最新价'].values[0])
                chg = float(row['涨跌幅'].values[0]) if '涨跌幅' in row.columns else 0
                name = str(row['名称'].values[0]) if '名称' in row.columns else ''
        except Exception:
            pass

        df = get_stock_daily_cached(code, 60)
        if df is None: return jsonify({'error': '数据不足'}), 404

        # 周末降级：用最近收盘价
        if cp is None:
            cp = float(df['close'].iloc[-1])
            chg = 0
        if not name:
            name = ''
        closes = df['close']; highs = df['high']; lows = df['low']
        rsi = compute_rsi(closes)
        macd_line, signal_line, macd_hist = compute_macd(closes)
        k, d, j = compute_kdj(highs, lows, closes)
        adx_val, plus_di, minus_di = compute_adx(highs, lows, closes) or (0, 0, 0)
        profit_pct = (cp - buy_price) / buy_price * 100 if buy_price > 0 else 0
        ma5 = closes.rolling(5).mean().iloc[-1]
        ma20 = closes.rolling(20).mean().iloc[-1]
        vol_ratio = float(df['volume'].iloc[-1] / df['volume'].rolling(20).mean().iloc[-1]) if len(df) >= 20 else 1.0

        # Composite signal (周末降级：跳过全量扫描)
        try:
            composite = calculate_comprehensive_score(code)
            current_signal = composite['signal'] if composite else 50
        except Exception:
            current_signal = 50

        should_sell = False; reasons = []; level = 'hold'

        if profit_pct >= 5:
            should_sell = True; level = 'strong_sell'
            reasons.append(f'盈利{profit_pct:.1f}%触发强制止盈线(+5%)')
        elif profit_pct >= 3:
            should_sell = True; level = 'sell'
            reasons.append(f'盈利{profit_pct:.1f}%触发止盈线(+3%)')
        elif profit_pct >= 1.5 and rsi > 70:
            should_sell = True; level = 'sell'
            reasons.append(f'盈利{profit_pct:.1f}%且RSI={rsi:.0f}超买')
        elif profit_pct <= -2:
            should_sell = True; level = 'strong_sell'
            reasons.append(f'亏损{profit_pct:.1f}%触发止损线(-2%)')
        elif rsi > 80:
            should_sell = True; level = 'sell'
            reasons.append(f'RSI={rsi:.0f}严重超买')
        elif rsi > 70 and macd_hist < 0:
            should_sell = True; level = 'sell'
            reasons.append(f'RSI={rsi:.0f}超买且MACD死叉')
        elif current_signal < 30:
            should_sell = True; level = 'sell'
            reasons.append(f'综合评分降至{current_signal}，信号失效')
        elif adx_val and adx_val > 30 and plus_di < minus_di:
            should_sell = True; level = 'sell'
            reasons.append(f'ADX={adx_val:.0f}趋势转空')
        elif ma5 < ma20 and profit_pct < 0:
            should_sell = True; level = 'sell'
            reasons.append('均线死叉且持仓亏损')

        if not should_sell:
            reasons.append(f'RSI={rsi:.0f} | 信号={current_signal} | 评分正常，可持有')

        return jsonify({
            'code': code, 'name': name, 'buy_price': buy_price,
            'current_price': cp, 'profit_pct': round(profit_pct, 2),
            'change_pct': chg, 'rsi': round(rsi, 1),
            'macd_hist': round(macd_hist, 4), 'kdj_k': round(k, 1),
            'adx': round(adx_val, 1) if adx_val else 0,
            'signal': current_signal, 'vol_ratio': round(vol_ratio, 2),
            'should_sell': should_sell, 'level': level,
            'reasons': reasons,
            'advice': '🔴 建议卖出' if level == 'strong_sell' else ('🟡 考虑卖出' if should_sell else '🟢 建议持有')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/position/buy', methods=['POST'])
def buy():
    data = request.get_json(); code=data.get('code','').strip(); price=float(data.get('price',0))
    if not code or price<=0: return jsonify({'error':'参数错误'}),400
    pos = load_positions()
    if any(p['code']==code for p in pos): return jsonify({'error':'已持有'}),400
    pos.append({'code':code,'buy_price':price,'buy_time':datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),'shares':100})
    save_positions(pos)
    return jsonify({'ok':True})

@app.route('/api/position/sell', methods=['POST'])
def sell():
    data = request.get_json(); code=data.get('code','').strip(); price=float(data.get('price',0))
    if not code or price<=0: return jsonify({'error':'参数错误'}),400
    pos = load_positions(); target = next((p for p in pos if p['code']==code),None)
    if not target: return jsonify({'error':'未持仓'}),400
    profit = (price-target['buy_price'])*target['shares']
    trade = {'code':code,'buy_price':target['buy_price'],'sell_price':price,'buy_time':target['buy_time'],'sell_time':datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),'profit':round(profit,2),'profit_pct':round((price-target['buy_price'])/target['buy_price']*100,2)}
    trades = load_trades(); trades.append(trade); save_trades(trades)
    pos.remove(target); save_positions(pos)
    try: _attribute_trade_to_factors(trade)
    except: pass
    return jsonify({'ok':True,'trade':trade})

@app.route('/api/stats', methods=['GET'])
def stats_route():
    return jsonify(calc_stats())

@app.route('/api/trades', methods=['GET'])
def get_trades_route():
    return jsonify(load_trades()[-50:])

@app.route('/api/diary', methods=['GET'])
def get_diary():
    return jsonify(load_json(DIARY_FILE, []))

@app.route('/api/diary', methods=['POST'])
def add_diary():
    data = request.get_json(); text = data.get('text','').strip()
    if not text: return jsonify({'error':'内容为空'}),400
    entries = load_json(DIARY_FILE, [])
    entry = {'id':len(entries)+1,'date':datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),'text':text,'reminder':extract_reminder(text)}
    entries.append(entry)
    cutoff = (datetime.datetime.now()-datetime.timedelta(days=180)).strftime('%Y-%m-%d')
    entries = [e for e in entries if e['date'][:10] >= cutoff]
    for i,e in enumerate(entries): e['id'] = i+1
    save_json(entries, DIARY_FILE)
    return jsonify(entry)

@app.route('/api/diary/<int:eid>', methods=['DELETE'])
def delete_diary(eid):
    entries = load_json(DIARY_FILE, [])
    entries = [e for e in entries if e['id']!=eid]
    for i,e in enumerate(entries): e['id'] = i+1
    save_json(entries, DIARY_FILE)
    return jsonify({'ok':True})

@app.route('/api/yiming', methods=['GET'])
def yiming():
    today = datetime.date.today()
    random.seed(today.year*10000+today.month*100+today.day)
    yiming_28 = [
        {'id':1,'text':'洒扫庭除，使身不近秽。','translation':'保持清洁，远离污秽。','summary':'整洁养气'},
        {'id':2,'text':'应时而兴，应时而食。','translation':'按规律作息饮食。','summary':'顺应天时'},
        {'id':3,'text':'言讷而实，语善而真。','translation':'说话谨慎真实，好运从口出。','summary':'慎言积福'},
        {'id':4,'text':'以见利之明处世，财可求也。','translation':'正当谋财，侵害他人必遭反报。','summary':'取财有道'},
        {'id':5,'text':'旧过过，未未到。戒之在贪。','translation':'过去已过，未来未到。忌贪多。','summary':'专注当下'},
        {'id':6,'text':'天时至，气运生，顺水行舟。','translation':'时机到了顺势而为。','summary':'顺势而为'},
        {'id':7,'text':'捷径塞途，康庄无人。','translation':'捷径拥挤，大道反而没人。','summary':'大道至简'},
        {'id':8,'text':'旧业勿轻弃，根基所系。','translation':'不轻易放弃旧业，那是根基。','summary':'守本创新'},
        {'id':9,'text':'初遇生人若厌憎，速避之。','translation':'不舒服的人远离。','summary':'择人而交'},
        {'id':10,'text':'父母者天授贵人，困厄援手亦贵人。','translation':'父母和困境中帮你的人都是贵人。','summary':'珍惜贵人'},
        {'id':11,'text':'事未成勿泄，利既获勿宣。','translation':'事成前保密，获利后不宣扬。','summary':'藏锋守拙'},
        {'id':12,'text':'当为之事竭诚以赴，苟且不如不为。','translation':'全力以赴，敷衍不如不做。','summary':'全力以赴'},
        {'id':13,'text':'识人涤尽浮光，惟观言行之微。','translation':'看人去光环，只观察言行。','summary':'透过表象'},
        {'id':14,'text':'御人当以疑始，旦暮异焉。','translation':'管理人先存疑，人善变。','summary':'审慎御人'},
        {'id':15,'text':'判事：下取利，中取鉴，上观气运。','translation':'判断成败看利益、教训、气运。','summary':'格局为重'},
        {'id':16,'text':'交游分三等：唯利、唯情、情利皆可。','translation':'朋友分三种类型。','summary':'交友分类'},
        {'id':17,'text':'时运至际乘势而起，骄矜则悖福。','translation':'好运顺势而为，骄傲背离福运。','summary':'乘势忌骄'},
        {'id':18,'text':'共事成在谋远、尽才、忘私。','translation':'合作靠谋划、人尽其才、无私。','summary':'忘私成事'},
        {'id':19,'text':'品性端良，试询隐衷，诚言为友。','translation':'品性好的人坦诚相告才是朋友。','summary':'以诚试友'},
        {'id':20,'text':'逢困厄自疏者有福，坦然而笑者大运。','translation':'困境自我疏导有福气。','summary':'困境从容'},
        {'id':21,'text':'众皆悦之险伏，人弃我取纳福。','translation':'大家追捧的藏险，被弃的是机会。','summary':'逆向思维'},
        {'id':22,'text':'夫妇信为基，同心御外。','translation':'夫妻重信任，同心抵御外患。','summary':'夫妻同心'},
        {'id':23,'text':'身者命之宅，不惜身宅坏。','translation':'身体是命的居所，要珍惜。','summary':'惜身惜福'},
        {'id':24,'text':'平等相对得真味，尊卑皆虚文。','translation':'真诚平等交流才有真话。','summary':'平等交流'},
        {'id':25,'text':'出资如博戏，必成者诈也。','translation':'投资如博弈，承诺必赚是骗人。','summary':'投资如博'},
        {'id':26,'text':'不欺暗室，不欺本心。','translation':'没人时也不欺己。','summary':'不欺本心'},
        {'id':27,'text':'勿求险事耽危娱，大伤气运。','translation':'不追求危险刺激，伤气运。','summary':'远离险乐'},
        {'id':28,'text':'心若自在，运自通达。','translation':'心自由，运气通达。','summary':'心自由运通'}
    ]
    return jsonify(random.choice(yiming_28))

@app.route('/api/pet_reminder', methods=['GET'])
def pet_reminder():
    entries = load_json(DIARY_FILE, [])
    if not entries: return jsonify({'text':'还没有复盘哦~'})
    reminders = entries[-1].get('reminder',[])
    return jsonify({'text':f"小橘子提醒：{random.choice(reminders)}"}) if reminders else jsonify({'text':'今天也要坚持复盘~'})

@app.route('/api/news', methods=['GET'])
def news():
    news_list = []
    today = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')

    try:
        express = ak.stock_info_global_em()
        if express is not None and len(express) > 0:
            for _, row in express.head(30).iterrows():
                title = str(row.get('title', ''))
                content = str(row.get('content', '')) if 'content' in row else ''
                news_time = str(row.get('datetime', ''))
                sectors = analyze_news_sectors(title, content)
                news_list.append({'title': title, 'time': news_time, 'source': '快讯', 'content': content, 'sectors': sectors})
    except Exception as e:
        print(f"东方财富快讯获取失败: {e}")

    try:
        import requests as req
        sina_url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=20&page=1"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = req.get(sina_url, headers=headers, timeout=10)
        sina_data = resp.json()
        for item in sina_data.get('result', {}).get('data', []):
            title = item.get('title', '')
            intro = item.get('intro', '')
            ctime = item.get('ctime', '')
            try:
                news_time = datetime.datetime.fromtimestamp(int(ctime)).strftime('%Y-%m-%d %H:%M')
            except:
                news_time = ''
            sectors = analyze_news_sectors(title, intro)
            news_list.append({'title': title, 'time': news_time, 'source': '新浪财经', 'content': intro, 'sectors': sectors})
    except Exception as e:
        print(f"新浪财经新闻获取失败: {e}")

    try:
        wd = today.weekday()
        cctv_date = today if wd < 5 else today - datetime.timedelta(days=wd - 4)
        cctv = ak.news_cctv(date=cctv_date.strftime('%Y%m%d'))
        if cctv is not None and len(cctv) > 0:
            for _, row in cctv.head(10).iterrows():
                title = str(row.get('title', ''))
                content = str(row.get('content', '')) if 'content' in row else ''
                sectors = analyze_news_sectors(title, content)
                news_list.append({'title': title, 'time': cctv_date.strftime('%Y%m%d'), 'source': '新闻联播', 'content': content, 'sectors': sectors})
    except: pass

    seen = set(); uniq = []
    for n in news_list:
        if n['title'] and n['title'] not in seen and len(n['title']) > 3:
            seen.add(n['title']); uniq.append(n)
    uniq.sort(key=lambda x: str(x.get('time', '')), reverse=True)
    today_news = [n for n in uniq if str(n.get('time', ''))[:10] == today_str]
    other = [n for n in uniq if str(n.get('time', ''))[:10] != today_str]
    return jsonify((today_news + other)[:30])

@app.route('/api/moneyflow', methods=['GET'])
def moneyflow():
    try:
        today = datetime.date.today(); wd = today.weekday()
        td = today-datetime.timedelta(days=1) if wd==5 else (today-datetime.timedelta(days=2) if wd==6 else today)
        df = ak.stock_board_industry_name_em()[['板块名称','涨跌幅','换手率','主力净流入']].dropna()
        df['涨跌幅'] = pd.to_numeric(df['涨跌幅'],errors='coerce')
        df['主力净流入'] = pd.to_numeric(df['主力净流入'],errors='coerce')
        df = df.dropna()
        return jsonify({'date':str(td),'top_inflow':df.nlargest(10,'主力净流入').to_dict('records'),'top_outflow':df.nsmallest(10,'主力净流入').to_dict('records')})
    except: return jsonify({'date':'','top_inflow':[],'top_outflow':[]})

@app.route('/api/heatmap', methods=['GET'])
def heatmap():
    try:
        today = datetime.date.today(); wd = today.weekday()
        if wd>=5: today = today-datetime.timedelta(days=wd-4)
        df = ak.stock_board_industry_name_em()[['板块名称','涨跌幅','换手率']].dropna()
        df['涨跌幅'] = pd.to_numeric(df['涨跌幅'],errors='coerce')
        df = df.dropna(subset=['涨跌幅']).sort_values('涨跌幅',ascending=False)
        top20=df.head(20); bottom5=df.tail(5)
        return jsonify({'date':str(today),'data':pd.concat([top20,bottom5]).drop_duplicates().to_dict('records')})
    except: return jsonify({'date':'','data':[]})

@app.route('/api/sector_rotation', methods=['GET'])
def sector_rotation():
    try:
        df = ak.stock_board_industry_name_em()[['板块名称','涨跌幅','换手率','主力净流入']].dropna()
        df['涨跌幅'] = pd.to_numeric(df['涨跌幅'], errors='coerce')
        df['主力净流入'] = pd.to_numeric(df['主力净流入'], errors='coerce')
        df = df.dropna()
        top_in = df.nlargest(5,'主力净流入')[['板块名称','主力净流入']].to_dict('records')
        return jsonify({'top_inflow':top_in,'advice':'建议关注主力资金持续流入的板块'})
    except: return jsonify({'error':'数据获取失败'})

@app.route('/api/market_ticker', methods=['GET'])
def market_ticker():
    """实时大盘指数（新浪源）"""
    import urllib.request
    try:
        url = "http://hq.sinajs.cn/list=sh000001,sz399001,sz399006,sh000300,sh000688"
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
            if len(values) >= 4:
                price = float(values[1]) if values[1] else 0
                yesterday = float(values[2]) if values[2] else 0
                change_pct = round((price - yesterday) / yesterday * 100, 2) if yesterday else 0
                result[code] = {"name": values[0], "price": round(price, 2), "change_pct": change_pct}
        return jsonify(result)
    except Exception as e:
        return jsonify({})


@app.route('/api/market_env', methods=['GET'])
def market_env_route():
    env = market_environment()
    return jsonify({'environment':env,'advice':{'bull':'强势市场，可加大仓位','bear':'弱势市场，建议轻仓','normal':'震荡市场，控制仓位'}.get(env,'')})

@app.route('/api/data_quality', methods=['GET'])
def data_quality():
    issues = []
    try:
        df = ak.stock_zh_a_spot_em()
        if len(df) < 1000: issues.append("股票池数量异常少")
        if df['最新价'].isna().sum()>0: issues.append("存在价格缺失")
    except Exception as e:
        issues.append(f"数据获取异常: {str(e)[:80]}")
    return jsonify({'issues':issues,'status':'正常' if not issues else '存在异常'})

@app.route('/api/signals_log', methods=['GET'])
def signals_log():
    return jsonify(load_json(SIGNALS_LOG_FILE, [])[-50:])

@app.route('/api/signals_log', methods=['POST'])
def add_signal_log():
    entry = request.get_json()
    log = load_json(SIGNALS_LOG_FILE, [])
    entry['time'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    log.append(entry)
    save_json(log, SIGNALS_LOG_FILE)
    return jsonify({'ok':True})

# ---- AI routes ----
@app.route('/api/ai/brief', methods=['GET'])
def ai_brief():
    if not AI_AVAILABLE: return jsonify({'brief':'AI服务未配置'})
    signals = load_json(SIGNALS_LOG_FILE, [])[-5:]
    try:
        news = ak.stock_info_global_em().head(10).to_dict('records')
    except:
        news = []
    return jsonify({'brief': ai_market_brief(news, signals, '')})

@app.route('/api/ai/enhanced_scan', methods=['POST'])
def ai_enhanced_scan():
    if not AI_AVAILABLE: return jsonify({'status':'error','message':'AI服务未配置'})
    if not is_trading_day(): return jsonify({'status':'non_trading','message':'今日非交易日'})
    pool = _get_pool_snapshot()
    if pool is None: return jsonify({'status':'error','message':'行情数据获取失败'})
    pool = pool[~pool['名称'].str.contains('ST|退')]
    pool = pool[pool['总市值']>50e8]; pool = pool[pool['最新价']>3]
    codes = pool['代码'].tolist()
    candidates = []
    with ProcessPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(calculate_comprehensive_score, c):c for c in codes}
        for fut in as_completed(futures):
            res = fut.result()
            if res and res['signal']>40:
                row = pool[pool['代码']==res['code']]
                if len(row)>0:
                    r = row.iloc[0]
                    res['name'] = r['名称']; res['price'] = r['最新价']; res['change_pct'] = r['涨跌幅']
                    candidates.append(res)
    candidates.sort(key=lambda x:x['signal'], reverse=True)
    try:
        news = ak.stock_info_global_em().head(15).to_dict('records')
    except:
        news = []
    ai_result = ai_enhanced_analysis(news, candidates[:5])
    return jsonify({'status':'ok','stocks':candidates[:15],'total':len(candidates),'ai_result':ai_result})

@app.route('/api/ai/committee', methods=['POST'])
def ai_committee():
    if not AI_AVAILABLE: return jsonify({'error': 'AI服务未配置'}), 503
    data = request.get_json()
    code = data.get('code', '').strip()
    if not code: return jsonify({'error': '请提供股票代码'}), 400

    stock_df = get_stock_daily_cached(code, 30)
    if stock_df is None: return jsonify({'error': '无法获取股票数据'}), 404

    closes = stock_df['close']
    stock_data = {
        'code': code,
        'latest_price': float(closes.iloc[-1]),
        'change_5d': round(float((closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5] * 100), 2),
        'rsi': round(compute_rsi(closes), 1),
        'volume_ratio': round(float(stock_df['volume_ratio'].iloc[-1]), 2) if 'volume_ratio' in stock_df.columns else 1.0,
        'ma5': round(float(stock_df['ma5'].iloc[-1]), 2) if 'ma5' in stock_df.columns else 0,
        'ma20': round(float(stock_df['ma20'].iloc[-1]), 2) if 'ma20' in stock_df.columns else 0,
        'amplitude': round(float((stock_df['high'].iloc[-1] - stock_df['low'].iloc[-1]) / closes.iloc[-1] * 100), 2)
    }
    try:
        news = ak.stock_info_global_em().head(10).to_dict('records')
    except:
        news = []
    positions = load_positions()
    result = ai_committee_decision(stock_data, news, positions)
    return jsonify(result)

@app.route('/api/ai/factor_mining', methods=['POST'])
def ai_factor_mining():
    try:
        pool = _get_pool_snapshot()
        if pool is None: return jsonify({'error': '行情数据获取失败，可能为非交易日'})
        pool = pool[~pool['名称'].str.contains('ST|退')]
        pool = pool[pool['总市值'] > 100e8]
        sample_codes = pool['代码'].head(30).tolist()

        sample_data = []
        for code in sample_codes[:10]:
            df = get_stock_daily_cached(code, 60)
            if df is not None and len(df) >= 30:
                closes = df['close'].values
                returns = (closes[-1] - closes[-20]) / closes[-20]
                volatility = np.std(df['close'].pct_change().dropna())
                volume_trend = df['volume'].iloc[-5:].mean() / df['volume'].iloc[-20:].mean()
                sample_data.append({
                    'code': code, 'return_20d': round(float(returns) * 100, 2),
                    'volatility': round(float(volatility), 4),
                    'volume_trend': round(float(volume_trend), 2),
                    'rsi': round(compute_rsi(df['close']), 1)
                })

        result = ai_factor_discovery(sample_data)
        return jsonify({'status': 'ok', 'sample_count': len(sample_data), 'ai_result': result})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/ai/evolve_factors', methods=['POST'])
def evolve_factors():
    try:
        pool = _get_pool_snapshot()
        if pool is None: return jsonify({'error': '行情数据获取失败，可能为非交易日'})
        pool = pool[~pool['名称'].str.contains('ST|退')]
        pool = pool[pool['总市值'] > 100e8]
        sample_codes = pool['代码'].head(15).tolist()

        sample_data = []
        for code in sample_codes[:8]:
            fdata = load_factor_data(code)
            if fdata:
                fdata['code'] = code
                sample_data.append(fdata)

        existing_names = list(DYNAMIC_FACTORS.keys())
        gene_pool = [
            {'name': k, 'category': v['category'], 'description': v['description'],
             'ic_30d': v.get('ic_30d', 0), 'active': v.get('active', False)}
            for k, v in FACTOR_REGISTRY.items()
        ]

        new_factor = ai_generate_factor_code(sample_data, existing_names, gene_pool)
        factor_name = new_factor.get('name', '')
        factor_formula = new_factor.get('formula', '')
        base_factors = new_factor.get('base_factors', [])

        if not factor_name or not factor_formula:
            return jsonify({'error': 'AI未生成有效因子', 'raw': str(new_factor)[:200]})

        factor_code = f"def {factor_name}(df):\n    return {factor_formula}"

        try:
            is_safe, safety_msg = validate_factor_code(factor_code, base_factors)
        except Exception as e:
            is_safe, safety_msg = False, f'安全校验异常: {str(e)}'
        if not is_safe:
            return jsonify({'error': f'安全校验失败: {safety_msg}'})

        # 重试最多3次，IC不够叫AI重新生成
        ic_value = None
        for attempt in range(3):
            try:
                ic_value = backtest_factor_ic(factor_code, factor_name)
            except Exception:
                ic_value = None
            if ic_value is not None and abs(ic_value) > 0.03:
                break
            if attempt < 2:
                retry_hint = f"上一次因子{'' if ic_value is None else 'IC='+str(round(ic_value,4))+'未达标'}，请生成更强的因子"
                new_factor = ai_generate_factor_code(sample_data, existing_names, gene_pool, retry_hint)
                factor_name = new_factor.get('name', '')
                factor_formula = new_factor.get('formula', '')
                if factor_name and factor_formula:
                    factor_code = f"def {factor_name}(df):\n    return {factor_formula}"

        if ic_value is None:
            return jsonify({'error': '因子IC回测失败，数据不足'})

        if abs(ic_value) <= 0.03:
            return jsonify({
                'status': 'rejected', 'factor_name': factor_name,
                'ic_value': round(ic_value, 4),
                'message': f'因子「{factor_name}」|IC|={abs(ic_value):.4f}≤0.03，未通过验证'
            })

        DYNAMIC_FACTORS[factor_name] = factor_code
        FACTOR_PERFORMANCE[factor_name] = {
            'ic': round(ic_value, 4), 'win_rate': 50, 'active': True,
            'created_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        }
        save_json(DYNAMIC_FACTORS, os.path.join(DATA_DIR, 'dynamic_factors.json'))
        save_json(FACTOR_PERFORMANCE, os.path.join(DATA_DIR, 'factor_performance.json'))

        FACTOR_REGISTRY[factor_name] = {
            'category': 'AI衍生', 'field': None,
            'description': f'AI从{",".join(base_factors[:3])}组合生成',
            'formula': factor_formula, 'weight': 1.0,
            'ic_30d': round(ic_value, 4), 'active': True,
            'base_factors': base_factors
        }
        save_registry()

        return jsonify({
            'status': 'ok', 'new_factor': new_factor,
            'ic_value': round(ic_value, 4), 'total_factors': len(DYNAMIC_FACTORS),
            'registry_size': len(FACTOR_REGISTRY),
            'message': f'新因子「{factor_name}」IC={ic_value:.4f}，已注册到因子库'
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/ai/factor_status', methods=['GET'])
def factor_status():
    return jsonify({'dynamic_factors': DYNAMIC_FACTORS, 'performance': FACTOR_PERFORMANCE})

# ---- 因子回测 ----
@app.route('/api/factor_backtest', methods=['POST'])
def factor_backtest():
    try:
        data = request.get_json() or {}
        factor_name = data.get('factor_name', '').strip()
        days = int(data.get('days', 60))
        if not factor_name: return jsonify({'error': '请指定因子名称'}), 400

        fmeta = FACTOR_REGISTRY.get(factor_name)
        if not fmeta: return jsonify({'error': f'因子「{factor_name}」不在注册表中'}), 404

        pool = _get_pool_snapshot()
        if pool is None: return jsonify({'error': '行情数据获取失败，请在工作日重试'})
        pool = pool[~pool['名称'].str.contains('ST|退')]
        pool = pool[pool['总市值'] > 80e8]
        sample_codes = pool['代码'].head(80).tolist()

        factor_values = []; future_returns = []; codes_used = []
        for code in sample_codes:
            fdata = load_factor_data(code)
            if not fdata or factor_name not in fdata: continue
            fv = fdata[factor_name]
            if not np.isfinite(fv): continue
            df = get_stock_daily_cached(code, days + 10)
            if df is None or len(df) < 5: continue
            ret_5d = (df['close'].iloc[-1] - df['close'].iloc[-5]) / df['close'].iloc[-5]
            factor_values.append(fv)
            future_returns.append(float(ret_5d))
            codes_used.append(code)

        if len(factor_values) < 30:
            return jsonify({'error': '有效数据不足（需≥30只股票）'})

        fv_arr = np.array(factor_values); fr_arr = np.array(future_returns)
        ic = np.corrcoef(fv_arr, fr_arr)[0, 1]
        n_groups = 5
        quantiles = np.percentile(fv_arr, np.linspace(0, 100, n_groups + 1))
        group_returns = []
        for g in range(n_groups):
            mask = (fv_arr >= quantiles[g]) & (fv_arr < quantiles[g + 1])
            if g == n_groups - 1: mask = fv_arr >= quantiles[g]
            group_returns.append(round(float(fr_arr[mask].mean()) * 100, 4) if mask.sum() > 0 else 0)

        long_short = round(group_returns[-1] - group_returns[0], 4)
        hit_rate = round(float((fv_arr * fr_arr > 0).mean()) * 100, 2)

        FACTOR_REGISTRY[factor_name]['ic_30d'] = round(float(ic), 4)
        save_registry()

        today_str = datetime.date.today().strftime('%Y-%m-%d')
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("INSERT OR REPLACE INTO factor_performance VALUES (?,?,?,?,?)",
                         (factor_name, today_str, round(float(ic), 6), round(float(ic), 6), long_short))
            conn.commit()
        except: pass
        finally: conn.close()

        return jsonify({
            'factor_name': factor_name, 'ic_mean': round(float(ic), 4),
            'sample_count': len(factor_values), 'group_returns': group_returns,
            'long_short_return': long_short, 'hit_rate': hit_rate,
            'ic_sign': '正向预测' if ic > 0 else '反向预测'
        })
    except Exception as e:
        return jsonify({'error': f'因子回测失败: {str(e)}'}), 500

@app.route('/api/factor_rank', methods=['GET'])
def factor_rank():
    try:
        ranked = []
        for name, meta in FACTOR_REGISTRY.items():
            if meta.get('active'):
                ranked.append({
                    'name': name, 'category': meta.get('category', '未知'),
                    'ic_30d': meta.get('ic_30d', 0), 'abs_ic': abs(meta.get('ic_30d', 0)),
                    'weight': meta.get('weight', 1.0), 'description': meta.get('description', '')
                })
        ranked.sort(key=lambda x: x['abs_ic'], reverse=True)
        return jsonify({'factors': ranked[:20], 'total': len(ranked)})
    except Exception as e:
        return jsonify({'error': f'因子排名失败: {str(e)}'}), 500

@app.route('/api/factor_registry', methods=['GET'])
def factor_registry_view():
    try:
        factors = []
        for name, meta in FACTOR_REGISTRY.items():
            factors.append({
                'name': name, 'category': meta.get('category', '未知'),
                'description': meta.get('description', ''),
                'weight': meta.get('weight', 1.0), 'ic_30d': meta.get('ic_30d', 0),
                'active': meta.get('active', False),
                'formula': meta.get('formula', ''), 'field': meta.get('field', '')
            })
        return jsonify({'factors': factors, 'total': len(factors)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/factor_registry', methods=['POST'])
def factor_toggle():
    try:
        data = request.get_json() or {}
        factor_name = data.get('factor_name', '').strip()
        active = data.get('active', True)
        if factor_name in FACTOR_REGISTRY:
            FACTOR_REGISTRY[factor_name]['active'] = active
            save_registry()
            return jsonify({'ok': True, 'factor_name': factor_name, 'active': active})
        return jsonify({'error': '因子不存在'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---- 回测 & 权重 ----
@app.route('/api/backtest', methods=['POST'])
def run_backtest():
    try:
        data = request.get_json() or {}
        days = int(data.get('days', 30)); top_n = int(data.get('top_n', 3))
        mode = data.get('mode', 'simple')
        train_ratio = float(data.get('train_ratio', 0.7))

        idx_df = get_index_daily("sh000300", days + 30)
        if idx_df is None: return jsonify({'error': '无法获取指数数据'})

        dates = sorted(idx_df['date'].unique())
        if len(dates) < days + 1: days = len(dates) - 2

        if mode == 'rolling':
            split_idx = int(len(dates) * train_ratio)
            train_dates = dates[:split_idx]; test_dates = dates[split_idx:]

            if len(train_dates) < 10 or len(test_dates) < 3:
                return jsonify({'error': '数据不足：训练集或测试集过小，请增大回测天数'})

            pool = _get_pool_snapshot()
            in_sample = []
            for d in train_dates[-min(30, len(train_dates))-1:-1]:
                in_sample.extend(_simulate_one_day(d, pool, top_n))
            out_sample = []
            for d in test_dates[-1:min(len(test_dates), len(test_dates))-1]:
                out_sample.extend(_simulate_one_day(d, pool, top_n))

            in_metrics = _compute_backtest_metrics(in_sample) if in_sample else None
            out_metrics = _compute_backtest_metrics(out_sample) if out_sample else None

            return jsonify({
                'mode': 'rolling', 'train_days': len(train_dates), 'test_days': len(test_dates),
                'in_sample': in_metrics, 'out_sample': out_metrics,
                'overfit_warning': _check_overfit(in_metrics, out_metrics)
            })

        pool = _get_pool_snapshot()
        if pool is None or len(pool) == 0:
            return jsonify({'error': '获取股票池失败，请在工作日重试'})

        results = []
        for d in dates[-days-1:-1]:
            results.extend(_simulate_one_day(d, pool, top_n))

        if not results:
            return jsonify({'error': '回测数据不足，请增大回测天数或减小每日选股数'})

        metrics = _compute_backtest_metrics(results)
        if metrics is None:
            return jsonify({'error': '统计指标计算失败'})

        ai_interpretation = ""
        if AI_AVAILABLE:
            try:
                summary_data = {k: metrics[k] for k in ['total_trades', 'win_rate', 'avg_return', 'max_drawdown', 'sharpe', 'cum_return']}
                ai_interpretation = ai_market_brief(
                    [{'title': f'回测结果：{json.dumps(summary_data, ensure_ascii=False)}'}], [], '请用通俗语言解读回测结果')
            except:
                ai_interpretation = "AI解读暂不可用"
        metrics['ai_interpretation'] = ai_interpretation
        return jsonify(metrics)
    except Exception as e:
        return jsonify({'error': f'回测异常: {str(e)}'})

@app.route('/api/update_weights', methods=['POST'])
def update_weights():
    try:
        updated_count = update_weights_internal()
        return jsonify({
            'ok': True, 'updated_factors': updated_count,
            'registry_size': len(FACTOR_REGISTRY),
            'strategy_weights': load_json(FACTOR_STATS_FILE, {})
        })
    except Exception as e:
        return jsonify({'error': f'权重更新失败: {str(e)}'}), 500

@app.route('/api/indicator_cycle', methods=['GET'])
def indicator_cycle():
    try:
        results = []
        for fname, fmeta in FACTOR_REGISTRY.items():
            if not fmeta.get('active'): continue
            cycle = detect_indicator_cycle(fname)
            results.append({
                'name': fname, 'category': fmeta.get('category', '未知'),
                'status': cycle.get('status', 'unknown'), 'ic_mean': cycle.get('ic_mean', 0),
                'recommendation': cycle.get('recommendation', ''),
                'ic_30d': fmeta.get('ic_30d', 0), 'weight': fmeta.get('weight', 1.0)
            })
        results.sort(key=lambda x: abs(x['ic_mean']), reverse=True)
        return jsonify({'factors': results, 'total': len(results)})
    except Exception as e:
        return jsonify({'error': f'周期检测失败: {str(e)}'}), 500

@app.route('/api/market_indicator_match', methods=['GET'])
def market_indicator_match():
    try:
        match = get_market_indicator_match()
        return jsonify(match)
    except Exception as e:
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

@app.route('/api/long_term_eval', methods=['POST'])
def long_term_eval():
    try:
        data = request.get_json() or {}
        years = int(data.get('years', 3))
        if years not in (1, 3, 5): years = 3

        benchmark = get_index_daily("sh000905", years * 252)
        if benchmark is None: return jsonify({'error': '无法获取中证500指数数据'})

        bench_dates = benchmark['date'].tolist()
        bench_closes = benchmark['close'].values
        bench_pct = benchmark['returns'].dropna().values

        pool = _get_pool_snapshot()
        sample_codes = pool['代码'].head(30).tolist() if pool is not None else []

        monthly_returns = []
        for code in sample_codes:
            df = get_stock_daily_cached(code, years * 252)
            if df is None: continue
            df['date_str'] = df['date'].apply(lambda x: str(x)[:7])
            monthly_groups = df.groupby('date_str')
            for _, month_df in monthly_groups:
                if len(month_df) < 10: continue
                start_close = month_df['close'].iloc[0]
                end_close = month_df['close'].iloc[-1]
                monthly_returns.append(float((end_close - start_close) / start_close))

        if len(monthly_returns) < 12:
            return jsonify({'error': '可用数据不足（需至少12个月度收益点）'})

        strategy_monthly = pd.Series(monthly_returns)
        annual_ret = float((1 + strategy_monthly.mean()) ** 12 - 1)
        annual_std = float(strategy_monthly.std() * (12 ** 0.5))
        sharpe = round((annual_ret - 0.02) / annual_std, 3) if annual_std > 0 else 0
        cum = (1 + strategy_monthly).cumprod()
        peak = cum.expanding().max()
        dd = (cum - peak) / peak
        max_dd = round(float(dd.min()) * 100, 2)
        calmar = round(annual_ret / abs(max_dd / 100), 3) if max_dd != 0 else 0

        bench_ret = float(np.mean(bench_pct) * 252)
        bench_std = float(np.std(bench_pct) * (252 ** 0.5))
        bench_sharpe = round((bench_ret - 0.02) / bench_std, 3) if bench_std > 0 else 0

        excess = round(annual_ret - bench_ret, 4)
        ir = round(excess / (strategy_monthly.std() * (12 ** 0.5)), 3) if strategy_monthly.std() > 0 else 0

        cum_vals = cum.tolist()
        n_points = min(len(cum_vals), 60)
        step = max(1, len(cum_vals) // n_points)
        curve = [round((v - 1) * 100, 2) for i, v in enumerate(cum_vals) if i % step == 0]

        bench_cum = (1 + pd.Series(bench_pct)).cumprod()
        bench_curve_vals = bench_cum.tolist()
        bench_step = max(1, len(bench_curve_vals) // n_points)
        bench_curve = [round((v - 1) * 100, 2) for i, v in enumerate(bench_curve_vals) if i % bench_step == 0]

        return jsonify({
            'years': years,
            'strategy': {
                'annual_return': round(annual_ret * 100, 2),
                'annual_volatility': round(annual_std * 100, 2),
                'sharpe_ratio': sharpe, 'max_drawdown': max_dd,
                'calmar_ratio': calmar, 'monthly_samples': len(strategy_monthly)
            },
            'benchmark': {'annual_return': round(bench_ret * 100, 2), 'sharpe_ratio': bench_sharpe},
            'comparison': {'excess_return': round(excess * 100, 2), 'information_ratio': ir},
            'equity_curve': curve[:60], 'benchmark_curve': bench_curve[:60]
        })
    except Exception as e:
        return jsonify({'error': f'长期评估失败: {str(e)}'}), 500

# ---- K线可视化 ----
@app.route('/api/kline/<code>', methods=['GET'])
def kline_chart(code):
    try:
        df = get_stock_daily_cached(code, 60)
        if df is None or len(df) < 20:
            return jsonify({'error': '数据不足'}), 404

        df = df.tail(30).reset_index(drop=True)
        closes = df['close'].values; opens = df['open'].values
        highs = df['high'].values; lows = df['low'].values
        dates = [str(d)[:10] for d in df['date'].values]

        ma5 = pd.Series(closes).rolling(5).mean().values
        ma10 = pd.Series(closes).rolling(10).mean().values
        ma20 = pd.Series(closes).rolling(20).mean().values

        fig, ax = plt.subplots(figsize=(14, 7), facecolor='#0a0a0a')
        ax.set_facecolor('#0a0a0a')

        width = 0.6
        for i in range(len(closes)):
            color = '#3aaf7c' if closes[i] >= opens[i] else '#d94a5d'
            body_bottom = min(opens[i], closes[i])
            body_height = abs(closes[i] - opens[i])
            if body_height < 0.001: body_height = 0.001
            ax.add_patch(Rectangle((i - width/2, body_bottom), width, body_height,
                                   facecolor=color, edgecolor=color, linewidth=0.5))
            ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=0.8)

        ax.plot(range(len(closes)), ma5, color='#e8a020', linewidth=1.2, label='MA5')
        ax.plot(range(len(closes)), ma10, color='#5b8cce', linewidth=1.2, label='MA10')
        ax.plot(range(len(closes)), ma20, color='#d94a5d', linewidth=1.2, label='MA20')

        for i in range(1, len(closes)):
            if all(not np.isnan(m[i]) and not np.isnan(m[i-1]) for m in [ma5, ma10]):
                if ma5[i-1] <= ma10[i-1] and ma5[i] > ma10[i]:
                    ax.annotate('金叉', (i, lows[i] - (highs[i]-lows[i])*0.3),
                                fontsize=8, color='#e8a020', ha='center',
                                bbox=dict(boxstyle='round,pad=0.2', facecolor='#1a1a1a', edgecolor='#e8a020', alpha=0.8))

        for i in range(20, len(closes)):
            if closes[i] >= opens[i]:
                if closes[i-1] <= ma5[i-1] and closes[i-1] <= ma10[i-1] and closes[i-1] <= ma20[i-1]:
                    if closes[i] > ma5[i] and closes[i] > ma10[i] and closes[i] > ma20[i]:
                        ax.annotate('蛟龙\n出海', (i, highs[i] + (highs[i]-lows[i])*0.2),
                                    fontsize=9, color='#ffc940', ha='center', weight='bold',
                                    bbox=dict(boxstyle='round,pad=0.3', facecolor='#2a0a00', edgecolor='#ffc940', alpha=0.9))

        ax.set_xticks(range(0, len(dates), max(1, len(dates)//8)))
        ax.set_xticklabels([dates[i] for i in range(0, len(dates), max(1, len(dates)//8))], color='#999', fontsize=9)
        ax.tick_params(axis='y', colors='#999')
        ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#1f1f1f'); ax.spines['bottom'].set_color('#1f1f1f')
        ax.grid(axis='y', color='#1a1a1a', linewidth=0.5)
        ax.legend(loc='upper left', fontsize=9, facecolor='#0a0a0a', edgecolor='#1f1f1f', labelcolor='#999')
        ax.set_title(f'{code} 近30日K线图', color='#e8a020', fontsize=14, fontweight='bold', pad=15)

        buf = BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png', dpi=100, facecolor='#0a0a0a', bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return jsonify({'code': code, 'image': 'data:image/png;base64,' + base64.b64encode(buf.read()).decode()})
    except Exception as e:
        return jsonify({'error': f'K线图生成失败: {str(e)}'}), 500

# ---- 策略组合 & 对比 ----
@app.route('/api/strategy_combo', methods=['GET'])
def strategy_combo():
    try:
        env = market_environment()
        combos = {
            'bull': {
                'environment': '强势市场',
                'primary': ['均线金叉', '多头趋势', '蛟龙出海'],
                'secondary': ['热点题材', '事件驱动'],
                'avoid': ['波浪理论'],
                'position': 70,
                'advice': '牛市环境中趋势策略最有效，蛟龙出海短期爆发力强，可加大仓位至70%'
            },
            'bear': {
                'environment': '弱势市场',
                'primary': ['缠论', '波浪理论', '上山爬坡'],
                'secondary': ['成长质量'],
                'avoid': ['均线金叉', '热点题材'],
                'position': 20,
                'advice': '熊市以防守为主，缠论底分型+上山爬坡寻找结构性机会，严格控制仓位20%以内'
            },
            'normal': {
                'environment': '震荡市场',
                'primary': ['缠论', '多头趋势', '上山爬坡'],
                'secondary': ['事件驱动', '预期重估', '蛟龙出海'],
                'avoid': ['波浪理论'],
                'position': 40,
                'advice': '震荡市需灵活应对，缠论抓转折+多头趋势确认方向，中等仓位40%为宜'
            }
        }
        combo = combos.get(env, combos['normal'])
        return jsonify({'environment': env, 'combo': combo})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/strategy_comparison', methods=['GET'])
def strategy_comparison():
    try:
        trades = load_trades()
        strategies = ['均线金叉', '缠论', '波浪理论', '多头趋势', '热点题材',
                      '事件驱动', '成长质量', '预期重估', '蛟龙出海', '上山爬坡']
        comparison = {}
        for s in strategies:
            comparison[s] = {'win_rate': 0, 'avg_return': 0, 'max_drawdown': 0, 'total_trades': 0, 'wins': 0}

        if trades:
            for t in trades:
                code = t['code']
                df = get_stock_daily_cached(code, 60)
                if df is None: continue
                closes = df['close']; highs = df['high']; lows = df['low']
                info = get_stock_info(code); sectors = get_stock_sector(code)
                adx_val, plus_di, minus_di = compute_adx(highs, lows, closes)

                s_scores = {
                    '均线金叉': strategy_ma_cross(code, closes),
                    '缠论': strategy_chan_theory(code, highs, lows, closes),
                    '波浪理论': strategy_wave_theory(code, highs, lows, closes),
                    '多头趋势': strategy_bull_trend(code, closes, adx_val, plus_di, minus_di),
                    '热点题材': strategy_hot_topic(code, sectors),
                    '事件驱动': strategy_event_driven(code),
                    '成长质量': strategy_growth_quality(code, info),
                    '预期重估': strategy_revaluation(code, info),
                    '蛟龙出海': strategy_dragon_rising(code, closes, highs, lows, df['open'] if 'open' in df.columns else closes),
                    '上山爬坡': strategy_mountain_climb(code, closes)
                }

                profit_pct = t.get('profit_pct', 0)
                is_win = t.get('profit', 0) > 0

                for name, score in s_scores.items():
                    if score >= 8:
                        comparison[name]['total_trades'] += 1
                        comparison[name]['avg_return'] += profit_pct
                        if is_win: comparison[name]['wins'] += 1

            for name, stats in comparison.items():
                if stats['total_trades'] > 0:
                    stats['win_rate'] = round(stats['wins'] / stats['total_trades'] * 100, 1)
                    stats['avg_return'] = round(stats['avg_return'] / stats['total_trades'], 2)
                else:
                    stats['win_rate'] = 0; stats['avg_return'] = 0

        return jsonify({'strategies': comparison, 'total_trades': len(trades)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---- 其他 ----
@app.route('/api/equity_curve', methods=['GET'])
def equity_curve():
    trades = load_trades()
    if not trades: return jsonify({'curve':[],'monthly':[],'drawdown':[]})
    sorted_trades = sorted(trades, key=lambda t: t.get('sell_time',''))
    curve = []; equity = 100000
    peak = 0; drawdown = []
    monthly_pnl = {}
    for t in sorted_trades:
        equity += t.get('profit',0)
        d = t.get('sell_time','')[:10]
        m = d[:7]
        curve.append({'date':d,'equity':round(equity,2)})
        peak = max(peak, equity)
        drawdown.append({'date':d,'drawdown':round((equity-peak)/peak*100,2) if peak > 0 else 0})
        monthly_pnl[m] = monthly_pnl.get(m,0) + t.get('profit',0)
    monthly_list = [{'month':k,'pnl':round(v,2)} for k,v in sorted(monthly_pnl.items())]
    return jsonify({'curve':curve,'monthly':monthly_list,'drawdown':drawdown})

@app.route('/api/report', methods=['GET'])
def report():
    trades = load_trades(); positions = load_positions()
    stats = calc_stats()
    report_text = f"交易报告\n总交易{stats['total_trades']}笔, 胜率{stats['win_rate']}%, 累计盈亏{stats['total_profit']}"
    if AI_AVAILABLE:
        try:
            diag = ai_strategy_diagnosis(trades, load_json(DIARY_FILE,[]))
            report_text += f"\n\nAI诊断:\n{diag}"
        except: pass
    return jsonify({'report':report_text})

@app.route('/api/dockerfile', methods=['GET'])
def dockerfile():
    docker = '''FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 5001
CMD ["python", "app.py"]'''
    return jsonify({'dockerfile':docker})

@app.route('/api/update', methods=['POST'])
def update():
    try:
        result = subprocess.run(['git','pull'], capture_output=True, text=True, cwd='.')
        return jsonify({'output':result.stdout,'error':result.stderr})
    except Exception as e:
        return jsonify({'error':str(e)})

@app.route('/api/refresh_data', methods=['POST'])
def refresh_data():
    """手动触发数据刷新"""
    try:
        n = refresh_all_daily_data()
        return jsonify({'ok': True, 'refreshed': n})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
