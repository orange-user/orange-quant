import time, datetime, random, json, os, sys, subprocess, threading
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

# DEPLOY_TOKEN可能不存在于旧版config.py，设后备值
try:
    DEPLOY_TOKEN
except NameError:
    DEPLOY_TOKEN = 'po2024'
from data import *
from data import _get_pool_snapshot
from engine import *
from lhb_strategy import generate_signals, fetch_lhb_data, get_today_signals, save_daily_signals
from alpha_factory import run_pipeline

# Try loading AI services
try:
    from ai_service import (ai_market_brief, ai_enhanced_analysis, ai_strategy_diagnosis,
                            ai_generate_factor_code, ai_factor_discovery,
                            ai_committee_decision, _deepseek_chat)
    AI_AVAILABLE = True
except Exception as e:
    print(f"AI services not available: {e}")
    AI_AVAILABLE = False

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.json.ensure_ascii = False  # JSON输出真实UTF-8中文而非\u转义

@app.after_request
def add_charset(response):
    """所有响应强制 charset=utf-8 + 禁用缓存(防Service Worker干扰)"""
    ct = response.headers.get('Content-Type', '')
    if ct and 'charset=' not in ct:
        response.headers['Content-Type'] = ct.rstrip('; ') + '; charset=utf-8'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

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

# ========== Ruflo MCP Server ==========
def _start_ruflo():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(('127.0.0.1', 3000))
        s.close()
        print('[Ruflo] MCP server already on port 3000')
        return
    except: pass
    finally: s.close()
    try:
        proc = subprocess.Popen(
            ['ruflo', 'mcp', 'start', '-t', 'http', '-p', '3000'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f'[Ruflo] Started MCP server (PID {proc.pid})')
    except Exception as e:
        print(f'[Ruflo] Failed: {e}')

_start_ruflo()

# ==================== WeChat Bot ====================
from wechat_bot import verify_signature, handle_message, WECHAT_TOKEN, _get_index_data

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
    if positions:
        try:
            df = _get_pool_snapshot()
            pos_codes = {p['code'] for p in positions}
            for _, row in df[df['代码'].isin(pos_codes)].iterrows():
                for p in positions:
                    if p['code'] == row['代码']:
                        cp = float(row['最新价'])
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
    """分析新闻涉及的板块和情感倾向"""
    text = title + content
    sector_map = {
        '电力':(['电力','电网','能源','发电','特高压','输电','算电协同'],'电力板块'),
        '半导体':(['芯片','半导体','集成电路','光刻','晶圆','算力'],'半导体板块'),
        'AI':(['人工智能','AI','大模型','深度学习','智能体'],'AI板块'),
        '新能源车':(['新能源车','电动车','锂电池','充电桩','固态电池'],'新能源车板块'),
        '银行':(['降准','降息','银行','信贷','利率','LPR'],'银行板块'),
        '房地产':(['房地产','房价','楼市','购房','收储'],'房地产板块'),
        '医药':(['医药','医疗','药品','疫苗','创新药'],'医药板块'),
        '消费':(['消费','零售','电商','家电','白酒','以旧换新'],'消费板块'),
        '军工':(['军工','国防','武器','军事','航天'],'军工板块'),
        '光伏':(['光伏','太阳能','硅料','储能'],'光伏板块'),
        '机器人':(['机器人','人形机器人','自动化','智能制造'],'机器人板块'),
        '低空经济':(['低空','无人机','飞行汽车','eVTOL'],'低空经济板块'),
        '数据要素':(['数据要素','数据资产','数据交易','东数西算'],'数据要素板块'),
        '量子计算':(['量子','量子计算','量子通信'],'量子计算板块'),
    }

    # 情感词库
    bull_words = ['利好','大涨','涨停','增长','突破','中标','签约','回购','增持','分红',
                  '放量','翻倍','新高','获批','政策支持','补贴','订单','超预期',
                  '扭亏','盈利','改善','复苏','反弹','开放','合作','放宽','降准','降息']
    bear_words = ['利空','大跌','跌停','暴跌','亏损','处罚','调查','制裁','关税',
                  '诉讼','退市','违约','减持','解禁','爆雷','风险','恶化','下滑',
                  '衰退','滞涨','收紧','加息','监管','问询','警示','停产','召回']

    # 判断情感
    bull_score = sum(1 for w in bull_words if w in text)
    bear_score = sum(1 for w in bear_words if w in text)
    if bull_score > bear_score: sentiment = '利好'
    elif bear_score > bull_score: sentiment = '利空'
    else: sentiment = '中性'

    # 匹配板块
    sectors = []
    for k, (kws, sec) in sector_map.items():
        if any(kw in text for kw in kws):
            sectors.append({'sector': sec, 'sentiment': sentiment})

    return {'sectors': sectors[:3], 'sentiment': sentiment}

def extract_reminder(text):
    kw = {'止损':'严格执行止损','追高':'避免追高','仓位':'注意仓位','贪婪':'克服贪婪','纪律':'坚守纪律','冲动':'避免冲动','耐心':'保持耐心','回撤':'控制回撤'}
    reminders = [rm for k,rm in kw.items() if k in text]
    return reminders[:3] if reminders else ['坚持复盘']


# ==================== 路由 ====================

@app.route('/')
@app.route('/go')
@app.route('/reload')
def index():
    return render_template('index.html')


@app.route('/api/status')
def api_status():
    """系统状态"""
    try:
        from wudao_client import get_remaining_calls, get_call_count
        remaining = get_remaining_calls()
        used = get_call_count()
    except:
        remaining = 0
        used = -1
    return jsonify({
        'status': 'ok',
        'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'data_source': {
            'primary': 'wudao' if used >= 0 else 'unknown',
            'fallback': 'akshare',
            'wudao_used': used,
            'wudao_remaining': remaining,
            'wudao_limit': 50,
        }
    })


@app.route('/api/wudao_status')
def wudao_status():
    """悟道调用状态"""
    try:
        from wudao_client import get_remaining_calls, get_call_count
        remaining = get_remaining_calls()
        used = get_call_count()
    except:
        remaining = 0
        used = -1
    return jsonify({
        'used': used,
        'remaining': remaining,
        'limit': 50,
        'pct': round(used / 50 * 100, 1) if used > 0 else 0,
    })


@app.route('/mobile')
def mobile():
    return render_template('mobile.html')

@app.route('/playground')
def playground():
    return render_template('playground.html')

# 扫描缓存（5分钟内不重复全量扫描）
_SCAN_CACHE = {'time': 0, 'data': None}
_scan_running = False
_scan_progress = {'pct': 0, 'stage': '', 'msg': ''}
_AUTO_TRADE_ENABLED = False  # 自动交易开关, 网页端可切换

def _set_progress(pct, stage, msg=''):
    global _scan_progress
    _scan_progress = {'pct': pct, 'stage': stage, 'msg': msg}

PLANET_POST_FILE = os.path.join('data', 'planet_post_today.txt')
PLANET_POST_LOG = os.path.join('data', 'planet_post_log.txt')

def _save_planet_post(candidates):
    """生成知识星球发帖纯文本"""
    import datetime as dt
    today = dt.date.today().strftime('%Y-%m-%d')
    wd = ['周一','周二','周三','周四','周五','周六','周日'][dt.date.today().weekday()]

    lines = [f'每日尾盘信号 | {today} {wd}', '']

    if not candidates:
        lines.append('今日无符合条件的信号。')
        lines.append('')
    else:
        for i, s in enumerate(candidates[:3]):
            chg = s.get('change_pct', 0)
            arrow = '📈' if chg >= 0 else '📉'
            sig = s.get('signal', 0)
            level = 'STRONG强烈买入' if sig >= 80 else ('BUY建议买入' if sig >= 65 else 'WATCH观察')

            risk = s.get('intraday_risk', False)
            risk_str = f' ⚠️{s["intraday_risk_reason"]}' if risk and s.get('intraday_risk_reason') else ''

            lines.append(f'TOP{i+1} {s.get("name","?")} {s.get("code","?")} {arrow} 信号{sig} {level}{risk_str}')
            lines.append(f'  价格 {s.get("price",0)}元 日内 {chg:+.2f}% 总市值{_fmt_mv(s.get("总市值",0))}')

            rsi = s.get('rsi', 0)
            vr = s.get('volume_ratio', 0)
            mom = s.get('momentum_5d', 0)
            adx = s.get('adx', 0)
            bb = s.get('bb_position', 0)
            bb_cn = _fmt_bb(bb) if bb else ''
            extra = ''
            if adx: extra += f' ADX {adx:.1f}'
            if bb_cn: extra += f' {bb_cn}'
            lines.append(f'  指标 RSI {rsi:.1f} 量比 {vr:.2f} 5日动量 {mom:+.1f}%{extra}')

            k, j = s.get('kdj_k'), s.get('kdj_j')
            mc = s.get('macd_hist', 0)
            if k: lines.append(f'  KDJ K{k:.1f} J{j:.1f} MACD柱{mc:+.4f}')

            cz = s.get('coint_z')
            if cz and abs(cz) > 1.5: lines.append(f'  协整Z {cz:.2f}（统计套利偏离）')

            reason = s.get('priority_reason', '')
            if reason: lines.append(f'  逻辑 {reason}')

            scores = s.get('strategy_scores', {})
            if scores:
                tops = [f'{k}{v}分' for k,v in sorted([x for x in scores.items() if x[1]>=8], key=lambda x:x[1], reverse=True)[:4]]
                if tops: lines.append(f'  策略 {" / ".join(tops)}')

            ai = s.get('ai_summary', '')
            if ai: lines.append(f'  AI {ai[:80]}')

            kp = s.get('kelly_pct', 0)
            if kp > 0: lines.append(f'  仓位 Kelly {kp:.0f}%')
            # 止盈止损（不写手数，用户自己按资金量算）
            stop_profit = 4 if sig >= 80 else 3
            stop_loss = 2
            lines.append(f'  止盈 {stop_profit}% 止损 {stop_loss}%（ATR动态调整）')
            lines.append('')

    # 持仓跟踪
    try:
        from config import load_json, POSITION_FILE
        pos = load_json(POSITION_FILE, [])
        if pos:
            lines.append('持仓跟踪：')
            pool = None
            for p in pos[:5]:
                code = p.get('code','')
                name = p.get('name','')
                bp = p.get('buy_price',0)
                bd = p.get('buy_date','')
                bs = p.get('buy_signal',0)
                cp = 0
                try:
                    if pool is None:
                        from data import _get_pool_snapshot
                        pool = _get_pool_snapshot()
                    row = pool[pool['代码']==code] if pool is not None and len(pool) > 0 else None
                    if row is not None and len(row) > 0:
                        cp = float(row['最新价'].values[0])
                except: pass
                if cp > 0 and bp > 0:
                    pnl = (cp - bp) / bp * 100
                    hold = ''
                    try:
                        d = (dt.date.today() - dt.datetime.strptime(bd, '%Y-%m-%d').date()).days
                        hold = f'T+{d}'
                    except: pass
                    tag = '高分' if bs >= 80 else ''
                    lines.append(f'  {code} {name} {hold} {pnl:+.1f}% {tag}')
            lines.append('')
    except: pass

    lines.append('明日计划：')
    lines.append('  09:25竞价观察 高开3%以上分批止盈 低开2%触发止损')
    lines.append('  持仓按T+1/T+2动态执行 高分票（信号>80）多持一天')
    lines.append('')
    lines.append('⚠️ 量化信号仅供参考 不构成投资建议 单票仓位不超过20%')
    lines.append(f'🔗 加入星球看完整内容：https://t.zsxq.com/mHlDz')

    text = '\n'.join(lines)
    os.makedirs('data', exist_ok=True)
    with open(PLANET_POST_FILE, 'w', encoding='utf-8') as f:
        f.write(text)
    try:
        with open(PLANET_POST_LOG, 'a', encoding='utf-8') as f:
            f.write(f'\n{"="*40}\n{today}\n{"="*40}\n{text}\n')
    except: pass
    print(f'[Planet] 已写入 {PLANET_POST_FILE}')
    return text

def _fmt_mv(mv):
    """格式化市值"""
    try:
        v = float(mv)
        if v >= 1e10: return f'{v/1e8:.0f}亿'
        if v >= 1e8: return f'{v/1e8:.1f}亿'
        return f'{v/1e4:.0f}万'
    except: return str(mv)

def _fmt_bb(pos):
    """布林带位置转中文"""
    if pos >= 1.0: return '布林上轨外'
    if pos >= 0.85: return '布林上轨附近'
    if pos >= 0.65: return '布林上轨偏下'
    if pos >= 0.45: return '布林中轨'
    if pos >= 0.25: return '布林中轨偏下'
    if pos >= 0.1: return '布林下轨附近'
    return '布林下轨外'

# 通用API缓存（减少akshare重复调用）
_API_CACHE = {}

def _cached_api(key, ttl=300):
    """返回缓存数据，若过期返回None"""
    entry = _API_CACHE.get(key)
    if entry and (time.time() - entry['time']) < ttl:
        return entry['data']
    return None

def _set_api_cache(key, data):
    _API_CACHE[key] = {'time': time.time(), 'data': data}

@app.route('/api/analyze', methods=['POST'])
def analyze():
    # 如果已有缓存且未过期，直接返回
    now = time.time()
    if _SCAN_CACHE['data'] and (now - _SCAN_CACHE['time']) < 300:
        cached = _SCAN_CACHE['data'].copy()
        cached['cached'] = True
        return jsonify(cached)

    if not is_trading_day():
        wd = get_weekday()
        msg = '周末休市，请在工作日进行扫描' if wd >= 5 else '今日非交易日，数据可能不是最新'
        return jsonify({'status': 'non_trading', 'message': msg, 'stocks': [], 'total': 0})

    # 如果已有扫描线程在跑，返回进度
    if _scan_running:
        return jsonify({'status': 'scanning', 'message': '扫描进行中，请等待...'})

    # 启动后台扫描
    def _bg_scan():
        global _scan_running, _SCAN_CACHE
        _scan_running = True
        # Watchdog: auto-reset after 180s
        def _watchdog():
            global _scan_running
            time.sleep(180)
            if _scan_running:
                _scan_running = False
                print("[watchdog] scan timeout 180s, auto reset")
        threading.Thread(target=_watchdog, daemon=True).start()
        try:
            _set_progress(0, '权重更新', '更新因子权重...')
            try: update_weights_internal()
            except: pass

            _set_progress(3, '协整', '发现协整配对...')
            try:
                from cointegration import load_coint_pairs
                cp = load_coint_pairs()
                print(f"[扫描] 协整配对: {len(cp)}个")
            except Exception as e:
                print(f"[扫描] 协整发现失败: {e}")

            _set_progress(5, '股票池', '获取全市场股票池...')
            pool = _get_pool_snapshot()
            if pool is None or len(pool) == 0:
                return
            pool = pool[~pool['名称'].str.contains('ST|退')]
            pool = pool[pool['总市值'] > 50e8]
            pool = pool[pool['最新价'] > 3]
            # 按资金量过滤买得起的股票（80%资金能买1手）
            from config import ACCOUNT_CAPITAL
            max_price = ACCOUNT_CAPITAL * 0.8 / 100
            pool = pool[pool['最新价'] <= max_price]
            pool = pool[pool['涨跌幅'].abs() < 9.5]
            # 量比过滤（仅当字段存在时）
            if '量比' in pool.columns:
                pool = pool[pool['量比'] > 0.8]  # 分析显示83%冲高股前日量比<1.5，>1.5过滤太严
            if '换手率' in pool.columns:
                pool = pool[(pool['换手率'] >= 5) & (pool['换手率'] <= 10)]
            elif 'turnoverratio' in pool.columns:
                pool = pool[(pool['turnoverratio'] >= 5) & (pool['turnoverratio'] <= 10)]
            else:
                print("[扫描] 无换手率字段，换手率过滤被跳过")

            pool['abs_chg'] = pool['涨跌幅'].abs()
            pool = pool.sort_values('abs_chg', ascending=False)
            codes = pool.head(300)['代码'].tolist()

            from concurrent.futures import ThreadPoolExecutor as TPE
            # Step 1: 快速预筛（仅SQLite缓存，不调akshare基本面）
            _set_progress(15, '预筛', f'快速预筛{len(codes)}只...')
            with TPE(max_workers=48) as ex:
                prescores = list(ex.map(quick_prescreen, codes))
            top_codes = [p['code'] for p in sorted(prescores, key=lambda x: x['prescore'], reverse=True)[:40]]

            # Step 2: 全量评分（只需40只）
            candidates = []
            total_todo = len(top_codes)
            done_count = 0
            _set_progress(30, '评分', f'全量评分0/{total_todo}...')
            with TPE(max_workers=48) as ex:
                futures = {ex.submit(calculate_comprehensive_score, c): c for c in top_codes}
                for fut in as_completed(futures):
                    try:
                        res = fut.result(timeout=30)
                        if res and res['signal'] > 30:
                            row = pool[pool['代码'] == res['code']]
                            if len(row) > 0:
                                r = row.iloc[0]
                                res['name'] = r['名称']; res['price'] = r['最新价']; res['change_pct'] = r['涨跌幅']
                                res['昨收'] = r.get('昨收', 0); res['今日最高'] = r.get('今日最高', 0); res['今日最低'] = r.get('今日最低', 0)
                                res['总市值'] = r.get('总市值', 0)
                                candidates.append(res)
                    except Exception:
                        pass
                    done_count += 1
                    if done_count % 5 == 0:
                        _set_progress(30 + int(50 * done_count / total_todo), '评分', f'全量评分{done_count}/{total_todo}...')

            # AI
            _set_progress(85, 'AI分析', 'AI委员会决策中...')
            if AI_AVAILABLE:
                try:
                    news_cache = get_global_news_cached()
                    ai_news = news_cache.head(10).to_dict('records') if news_cache is not None else []
                except:
                    ai_news = []
                positions_cache = load_positions()
                for candidate in candidates[:5]:
                    try:
                        stock_data = {
                            'code': candidate['code'], 'latest_price': candidate['price'],
                            'change_pct': candidate['change_pct'], 'rsi': candidate['rsi'],
                            'adx': candidate['adx'], 'volume_ratio': candidate['volume_ratio'],
                            'momentum_5d': candidate['momentum_5d'],
                            'priority_reason': candidate['priority_reason'],
                            'basic_info': candidate.get('basic_info', {})
                        }
                        ai_result = ai_committee_decision(stock_data, ai_news, positions_cache)
                        candidate['ai_decision'] = ai_result.get('decision', 'PASS')
                        candidate['ai_summary'] = ai_result.get('summary', '')
                    except Exception:
                        pass

            _set_progress(95, 'AI分析', 'AI决策完成，整理结果...')
            # 日内涨幅调整：仅对上涨有效，3-5%加分，>7%扣分
            for c in candidates:
                chg = c.get('change_pct', 0)
                if chg < 0:
                    c['signal'] = int(c['signal'] * 0.85)  # 下跌扣分
                elif chg < 3:
                    c['signal'] = int(c['signal'] * 0.90)
                elif chg <= 5:
                    c['signal'] = min(100, int(c['signal'] * 1.15))
                elif chg <= 7:
                    pass
                else:
                    c['signal'] = int(c['signal'] * 0.70)
            # 分时危险形态检测（炸板、高开低走、弱势收盘、放量滞涨）
            for c in candidates:
                yc = c.get('昨收', 0)
                dh = c.get('今日最高', 0)
                dl = c.get('今日最低', 0)
                pr = c.get('price', 0)
                chg = c.get('change_pct', 0)
                vr = c.get('volume_ratio', 1)
                max_chg = (dh - yc) / yc * 100 if yc and dh > 0 else 0
                pullback = max_chg - chg if max_chg > 0 else 0
                # 日内位置：(现价-最低)/(最高-最低)，0=最低点 100=最高点
                pos = (pr - dl) / (dh - dl) * 100 if dh > dl and dh > 0 else 50
                reasons = []
                if max_chg >= 9.0 and pullback >= 3:
                    c['signal'] = int(c['signal'] * 0.55)
                    reasons.append('炸板')
                elif max_chg >= 9.0 and pullback >= 1.5:
                    c['signal'] = int(c['signal'] * 0.75)
                    reasons.append('冲高回落')
                elif max_chg >= 8.0 and pullback >= 2:
                    c['signal'] = int(c['signal'] * 0.85)
                    reasons.append('明显回落')
                if chg > 0 and pos < 25 and max_chg >= 1.5:
                    c['signal'] = int(c['signal'] * 0.70)
                    reasons.append('高开低走')
                if pos < 15 and chg < 2:
                    c['signal'] = int(c['signal'] * 0.80)
                    reasons.append('弱势收盘')
                if vr > 2.0 and chg < 1:
                    c['signal'] = int(c['signal'] * 0.80)
                    reasons.append('放量滞涨')
                c['intraday_risk'] = True if reasons else False
                c['intraday_risk_reason'] = ';'.join(reasons) if reasons else ''
                # AL Brooks 背景上下文警告
                b = c.get('brooks', {})
                if b.get('context') == 'trading_range' and c.get('signal', 0) > 70:
                    c['intraday_risk'] = True
                    c['intraday_risk_reason'] = (c.get('intraday_risk_reason', '') + ';震荡背景·突破信号降权').strip(';')
                    c['signal'] = int(c['signal'] * 0.90)
            # 凯利公式仓位 + 协整信息
            for c in candidates:
                try:
                    from kelly import calc_kelly_position
                    k = calc_kelly_position(c['code'], c['price'], c['signal'])
                    c['kelly_pct'] = k['kelly_pct']
                    c['kelly_shares'] = k['suggested_shares']
                    c['position_advice'] = f"Kelly{k['kelly_pct']}% ({k['suggested_shares']}手)"
                except:
                    pass
                try:
                    from cointegration import get_cointegration_score
                    _, z, det = get_cointegration_score(c['code'])
                    if z is not None:
                        c['coint_z'] = z
                    if det:
                        c['coint_pair'] = det.get('pair_code', '')
                except:
                    pass
            # 分数排名归一化
            _sig_vals = [c['signal'] for c in candidates if isinstance(c.get('signal'), (int, float))]
            if _sig_vals:
                _min_s, _max_s = min(_sig_vals), max(_sig_vals)
                if _max_s > _min_s:
                    for c in candidates:
                        c['signal'] = int((c['signal'] - _min_s) / (_max_s - _min_s) * 99 + 1)
                else:
                    for c in candidates:
                        c['signal'] = 50
            candidates.sort(key=lambda x: x['signal'], reverse=True)
            _set_progress(100, '完成', '扫描完成')
            _SCAN_CACHE['time'] = time.time()
            _SCAN_CACHE['data'] = {'status': 'ok', 'stocks': candidates[:15], 'total': len(candidates)}
            # 持久化扫描结果
            try:
                from config import SIGNALS_LOG_FILE, save_json
                signals_log = load_json(SIGNALS_LOG_FILE, [])
                signals_log.append({
                    'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'date': datetime.date.today().strftime('%Y-%m-%d'),
                    'stocks': candidates[:15]
                })
                if len(signals_log) > 90:
                    signals_log = signals_log[-60:]
                save_json(signals_log, SIGNALS_LOG_FILE)
            except Exception:
                pass

            # Ruflo: store TOP5 to memory
            try:
                from mcp_client import safe_store
                for c in candidates[:5]:
                    safe_store(
                        f"Stock {c['code']}: sig={c['signal']}, rsi={c.get('rsi',0)}, "
                        f"vr={c.get('volume_ratio',0)}, mom={c.get('momentum_5d',0)}",
                        {'code': c['code'], 'signal': c['signal'], 'price': c.get('price',0),
                         'rsi': c.get('rsi',0), 'date': str(datetime.date.today()),
                         'name': c.get('name','')}
                    )
                print(f'[Ruflo] Stored {min(5,len(candidates))} patterns')
            except Exception as e:
                print(f'[Ruflo] Store error: {e}')

            # 生成星球发帖文本（供Hermes推送微信）
            try:
                _save_planet_post(candidates[:5])
            except Exception as e:
                print(f'[Planet] 发帖生成失败: {e}')

            # 自动交易: 扫描完成后自动买入TOP1
            if candidates and _AUTO_TRADE_ENABLED:
                try:
                    top = candidates[0]
                    import subprocess
                    code = top['code']; price = top['price']
                    from kelly import calc_kelly_position
                    k = calc_kelly_position(code, price, min(round(top['signal']), 100))
                    shares = k['suggested_shares']
                    print(f'[自动交易] 买入 {top["name"]}({code}) {price}×{shares}')
                    result = subprocess.run(
                        [sys.executable, '-c',
                         f'from trader import PulseTrader; t=PulseTrader(); t.connect(); t.buy("{code}",{price},{shares})'],
                        capture_output=True, text=True, timeout=30
                    )
                    print(f'[自动交易] 结果: {result.stdout[:100] if result.stdout else "ok"}')
                except Exception as ate:
                    print(f'[自动交易] 失败: {ate}')

        except Exception as e:
            _SCAN_CACHE['data'] = {'status': 'error', 'message': f'扫描异常: {str(e)[:80]}', 'stocks': [], 'total': 0}
        finally:
            _scan_running = False

    threading.Thread(target=_bg_scan, daemon=True).start()
    return jsonify({'status': 'scanning', 'message': '扫描已启动，预计60-90秒完成，请稍后查看...'})


@app.route('/api/auto_trade', methods=['GET', 'POST'])
def auto_trade():
    """自动交易开关"""
    global _AUTO_TRADE_ENABLED
    if request.method == 'POST':
        data = request.get_json() or {}
        _AUTO_TRADE_ENABLED = data.get('enabled', False)
        print(f'[自动交易] 开关: {_AUTO_TRADE_ENABLED}')
        return jsonify({'auto_trade': _AUTO_TRADE_ENABLED})
    return jsonify({'auto_trade': _AUTO_TRADE_ENABLED})


@app.route('/api/scan_status', methods=['GET'])
def scan_status():
    """轮询扫描状态"""
    if _scan_running:
        return jsonify({'status': 'scanning', 'message': _scan_progress['msg'], 'progress': _scan_progress})
    now = time.time()
    if _SCAN_CACHE['data'] and (now - _SCAN_CACHE['time']) < 3600:
        cached = _SCAN_CACHE['data'].copy()
        cached['cached'] = _SCAN_CACHE['time'] > now - 300
        return jsonify(cached)
    return jsonify({'status': 'idle', 'message': '点击扫描开始'})

@app.route('/api/sell_check', methods=['POST'])
def sell_check():
    """卖出检查：支持多日持有策略（T+1观察，T+2强制卖出）"""
    data = request.get_json()
    code = data.get('code','').strip()
    buy_price = float(data.get('buy_price',0))
    buy_date_str = data.get('buy_date', '')  # 可选，用于多日策略
    buy_signal = data.get('buy_signal', 0)  # 买入时的评分，用于信号衰减对比
    if not code: return jsonify({'error':'缺少代码'}),400
    try:
        cp = chg = name = None
        try:
            spot = _get_pool_snapshot()
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
        # ATR动态止损
        try:
            from engine import ArrayManager
            am = ArrayManager(60)
            for i in range(len(closes)):
                am.update_bar(df['open'].iloc[i], df['high'].iloc[i], df['low'].iloc[i], df['close'].iloc[i], df['volume'].iloc[i])
            atr_val = am.atr(14)
            atr_pct = atr_val / cp if cp > 0 else 0
            atr_stop = max(atr_pct * 2, 0.02)
        except:
            atr_stop = 0.03

        # Composite signal
        try:
            composite = calculate_comprehensive_score(code)
            current_signal = composite['signal'] if composite else 50
        except Exception:
            current_signal = 50

        should_sell = False; reasons = []; level = 'hold'
        holding_days = 99

        # === 多日持有策略（T+1观察，T+2强制卖出）===
        if buy_date_str:
            try:
                bd = datetime.datetime.strptime(buy_date_str, '%Y-%m-%d').date()
                today = datetime.date.today()
                holding_days = (today - bd).days
            except:
                holding_days = 99

        if buy_date_str and holding_days < 99:
            if holding_days >= 2:
                # T+2+：按信号强度决定持有上限
                if buy_signal >= 80 and profit_pct >= -1:
                    # 高分信号：允许持有到T+3
                    if holding_days >= 3:
                        should_sell = True; level = 'strong_sell'
                        reasons.append(f'T+{holding_days}高分信号持有上限，强制卖出(盈利{profit_pct:.1f}%)')
                    else:
                        reasons.append(f'T+2高分信号持续有效，明日09:26强制卖出(盈利{profit_pct:.1f}%)')
                else:
                    should_sell = True; level = 'strong_sell'
                    reasons.append(f'T+{holding_days}强制卖出(盈利{profit_pct:.1f}%)')
            elif holding_days == 1:
                # T+1 有条件持有
                now_h = datetime.datetime.now().hour
                if profit_pct >= 2.5:
                    if buy_signal >= 80 and profit_pct < 4:
                        # 高分信号：提高止盈线到4%
                        pass
                    else:
                        should_sell = True; level = 'strong_sell'
                        reasons.append(f'T+1盈利{profit_pct:.1f}%触发止盈线(+2.5%)')
                elif profit_pct <= -atr_stop*100:
                    should_sell = True; level = 'strong_sell'
                    reasons.append(f'T+1亏损{profit_pct:.1f}%触发ATR动态止损({atr_stop:.1%})')
                elif rsi > 80 and profit_pct > 0:
                    should_sell = True; level = 'sell'
                    reasons.append(f'T+1 RSI={rsi:.0f}超买，建议止盈')
                # T+1信号衰减检查：买入评分对比当前评分
                elif buy_signal > 10 and current_signal < buy_signal * 0.6:
                    if buy_signal >= 80:
                        # 高分信号：信号衰减容忍度高，降级不强制卖
                        reasons.append(f'评分从{buy_signal}跌至{current_signal}，高分信号观察中')
                    else:
                        should_sell = True;
                        decay = (buy_signal - current_signal) / buy_signal
                        level = 'strong_sell' if now_h >= 14 else 'sell'
                        reasons.append(f'评分从{buy_signal}跌至{current_signal}(-{decay:.0%})，信号失效')
                elif now_h >= 14 and now_h < 15:
                    # T+1尾盘：亏损>2%止损，否则持有到T+2
                    if profit_pct <= -atr_stop*100:
                        should_sell = True; level = 'strong_sell'
                        reasons.append(f'T+1尾盘亏损{profit_pct:.1f}%，ATR止损({atr_stop:.1%})')
                    else:
                        reasons.append(f'T+1尾盘信号正常，明日09:26强制卖出')
                else:
                    # T+1早盘：观察中
                    reasons.append(f'T+1观察中 RSI={rsi:.0f} 明日09:26强制卖出')
            else:
                # T+0 刚买入
                reasons.append(f'今日刚买入 {name}，明日开盘后观察')
        else:
            # === 传统单日策略（无buy_date时回退）===
            if profit_pct >= 5:
                should_sell = True; level = 'strong_sell'
                reasons.append(f'盈利{profit_pct:.1f}%触发强制止盈线(+5%)')
            elif profit_pct >= 3:
                should_sell = True; level = 'sell'
                reasons.append(f'盈利{profit_pct:.1f}%触发止盈线(+3%)')
            elif profit_pct >= 1.5 and rsi > 70:
                should_sell = True; level = 'sell'
                reasons.append(f'盈利{profit_pct:.1f}%且RSI={rsi:.0f}超买')
            elif profit_pct <= -atr_stop*100:
                should_sell = True; level = 'strong_sell'
                reasons.append(f'亏损{profit_pct:.1f}%触发ATR止损({atr_stop:.1%})')
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
            # 均线死叉卖出 (从abu移植)
            if ma5 < ma20 and profit_pct < 0:
                should_sell = True; level = 'sell'
                reasons.append('均线死叉且持仓亏损')
            # 浮盈回撤保护: 从持仓最高点回撤超过1.5倍ATR
            if profit_pct > 3 and atr_pct > 0:
                pullback = profit_pct - (cp - buy_price * 1.01) / buy_price * 100
                if pullback > atr_pct * 150:
                    should_sell = True; level = 'sell'
                    reasons.append(f'浮盈回撤{pullback:.1f}%超过ATR阈值,止盈')
            elif ma5 < ma20 and profit_pct < 0:
                should_sell = True; level = 'sell'
                reasons.append('均线死叉且持仓亏损')

        if not should_sell and not reasons:
            reasons.append(f'RSI={rsi:.0f} | 信号={current_signal} | 评分正常，可持有')

        return jsonify({
            'code': code, 'name': name, 'buy_price': buy_price,
            'current_price': cp, 'profit_pct': round(profit_pct, 2),
            'change_pct': chg, 'rsi': round(rsi, 1),
            'macd_hist': round(macd_hist, 4), 'kdj_k': round(k, 1),
            'adx': round(adx_val, 1) if adx_val else 0,
            'signal': current_signal, 'vol_ratio': round(vol_ratio, 2),
            'should_sell': should_sell, 'level': level,
            'reasons': reasons, 'holding_days': holding_days,
            'advice': '🔴 建议卖出' if level == 'strong_sell' else ('🟡 考虑卖出' if should_sell else '🟢 建议持有')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/position/buy', methods=['POST'])
def buy():
    data = request.get_json(); code=data.get('code','').strip(); price=float(data.get('price',0))
    shares = int(data.get('shares', 0))  # 0=自动凯利计算
    if not code or price<=0: return jsonify({'error':'参数错误'}),400
    if shares < 1:
        try:
            from kelly import calc_kelly_position
            k = calc_kelly_position(code, price, data.get('signal', 50))
            shares = k['suggested_shares']
        except:
            shares = 1
    pos = load_positions()
    if any(p['code']==code for p in pos): return jsonify({'error':'已持有'}),400
    now = datetime.datetime.now()
    pos.append({'code':code,'buy_price':price,'buy_time':now.strftime('%Y-%m-%d %H:%M'),'buy_date':now.strftime('%Y-%m-%d'),'shares':shares,'buy_signal':data.get('signal',0)})
    save_positions(pos)
    _API_CACHE.pop('stats', None)
    return jsonify({'ok':True, 'shares':shares})

@app.route('/api/position/sell', methods=['POST'])
def sell():
    data = request.get_json(); code=data.get('code','').strip(); price=float(data.get('price',0))
    if not code or price<=0: return jsonify({'error':'参数错误'}),400
    pos = load_positions(); target = next((p for p in pos if p['code']==code),None)
    if not target: return jsonify({'error':'未持仓'}),400
    profit = (price-target['buy_price'])*target['shares']
    trade = {'code':code,'buy_price':target['buy_price'],'sell_price':price,'buy_time':target['buy_time'],'sell_time':datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),'profit':round(profit,2),'profit_pct':round((price-target['buy_price'])/target['buy_price']*100,2)}
    trades = load_trades(); trades.append(trade)
    pos.remove(target)
    save_transaction(pos, trades)
    try: _attribute_trade_to_factors(trade)
    except: pass
    try: update_weights_internal()
    except: pass
    _API_CACHE.pop('stats', None)
    _API_CACHE.pop('factor_learning', None)
    return jsonify({'ok':True,'trade':trade})

@app.route('/api/position/<code>', methods=['DELETE'])
def delete_position(code):
    """直接删除持仓，不留交易记录"""
    pos = load_positions()
    target = next((p for p in pos if p['code']==code), None)
    if not target: return jsonify({'error':'未持仓'}), 404
    pos.remove(target)
    save_positions(pos)
    _API_CACHE.pop('stats', None)
    return jsonify({'ok':True})

@app.route('/api/sell_strategy', methods=['GET'])
def sell_strategy_all():
    """批量生成所有持仓的买卖策略建议（供09:26推送调用）"""
    from config import load_json
    pos = load_json('data/positions.json', [])
    results = []
    now_h = datetime.datetime.now().hour
    for p in pos:
        try:
            url = f"http://127.0.0.1:5001/api/sell_check"
            import requests
            resp = requests.post(url, json={
                'code': p['code'], 'buy_price': p['buy_price'],
                'buy_date': p.get('buy_date', p.get('buy_time', '')[:10]),
                'buy_signal': p.get('buy_signal', 0)
            }, timeout=15)
            r = resp.json()
            r['shares'] = p.get('shares', 1)
            results.append(r)
        except Exception as e:
            results.append({'code': p['code'], 'error': str(e)})
    return jsonify({'positions': results, 'hour': now_h, 'count': len(results)})

@app.route('/api/stats', methods=['GET'])
def stats_route():
    cached = _cached_api('stats', 60)
    if cached: return jsonify(cached)
    result = calc_stats()
    _set_api_cache('stats', result)
    return jsonify(result)

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
    cached = _cached_api('news', 120)
    if cached: return jsonify(cached)
    news_list = []
    today = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')

    # 并发抓取三个新闻源
    def fetch_eastmoney():
        items = []
        try:
            express = ak.stock_info_global_em()
            if express is not None and len(express) > 0:
                for _, row in express.head(30).iterrows():
                    title = str(row.get('title', ''))
                    content = str(row.get('content', '')) if 'content' in row else ''
                    news_time = str(row.get('datetime', ''))
                    analysis = analyze_news_sectors(title, content)
                    items.append({'title': title, 'time': news_time, 'source': '快讯',
                                  'content': content, 'sentiment': analysis['sentiment'],
                                  'sectors': analysis['sectors']})
        except Exception as e:
            print(f"东方财富快讯获取失败: {e}")
        return items

    def fetch_sina():
        items = []
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
                analysis = analyze_news_sectors(title, intro)
                items.append({'title': title, 'time': news_time, 'source': '新浪财经',
                              'content': intro, 'sentiment': analysis['sentiment'],
                              'sectors': analysis['sectors']})
        except Exception as e:
            print(f"新浪财经新闻获取失败: {e}")
        return items

    def fetch_cctv():
        items = []
        try:
            wd = today.weekday()
            cctv_date = today if wd < 5 else today - datetime.timedelta(days=wd - 4)
            cctv = ak.news_cctv(date=cctv_date.strftime('%Y%m%d'))
            if cctv is not None and len(cctv) > 0:
                for _, row in cctv.head(10).iterrows():
                    title = str(row.get('title', ''))
                    content = str(row.get('content', '')) if 'content' in row else ''
                    analysis = analyze_news_sectors(title, content)
                    items.append({'title': title, 'time': cctv_date.strftime('%Y-%m-%d') + ' 19:00',
                                  'source': '新闻联播', 'content': content,
                                  'sentiment': analysis['sentiment'],
                                  'sectors': analysis['sectors']})
        except: pass
        return items

    results = {}
    threads = [
        threading.Thread(target=lambda: results.update({'eastmoney': fetch_eastmoney()})),
        threading.Thread(target=lambda: results.update({'sina': fetch_sina()})),
        threading.Thread(target=lambda: results.update({'cctv': fetch_cctv()})),
    ]
    for t in threads: t.start()
    for t in threads: t.join(timeout=12)

    for key in ['eastmoney', 'sina', 'cctv']:
        news_list.extend(results.get(key, []))

    # 去重排序
    seen = set(); uniq = []
    for n in news_list:
        if n['title'] and n['title'] not in seen and len(n['title']) > 3:
            seen.add(n['title']); uniq.append(n)
    uniq.sort(key=lambda x: str(x.get('time', '')), reverse=True)
    today_news = [n for n in uniq if str(n.get('time', ''))[:10] == today_str]
    other = [n for n in uniq if str(n.get('time', ''))[:10] != today_str]
    result = (today_news + other)[:30]

    # AI 摘要：取前10条用DeepSeek生成市场简报
    ai_digest = ''
    if AI_AVAILABLE and len(today_news) >= 3:
        try:
            digest_text = '\n'.join([f"- [{n['source']}] {n['title']}" for n in (today_news + other)[:10]])
            ai_digest = _deepseek_chat([
                {"role": "system", "content": "用3-5句话总结这些新闻对A股的影响，重点说哪些板块受益、哪些要回避。简洁有力，不超过150字。"},
                {"role": "user", "content": digest_text}
            ], max_tokens=200)
        except: pass

    result = {'items': result, 'ai_digest': ai_digest or ''}
    _set_api_cache('news', result)
    return jsonify(result)

@app.route('/api/moneyflow', methods=['GET'])
def moneyflow():
    cached = _cached_api('moneyflow', 120)
    if cached: return jsonify(cached)
    try:
        today = datetime.date.today(); wd = today.weekday()
        td = today-datetime.timedelta(days=1) if wd==5 else (today-datetime.timedelta(days=2) if wd==6 else today)
        df = ak.stock_fund_flow_industry()
        df = df.rename(columns={'行业': '板块名称', '行业-涨跌幅': '涨跌幅', '净额': '主力净流入', '流入资金': '流入', '流出资金': '流出'})
        df['涨跌幅'] = pd.to_numeric(df['涨跌幅'], errors='coerce')
        df['主力净流入'] = pd.to_numeric(df['主力净流入'], errors='coerce') * 1e8  # 亿元 -> 元
        df = df.dropna(subset=['涨跌幅', '主力净流入'])
        result = {'date':str(td),'top_inflow':df.nlargest(10,'主力净流入').to_dict('records'),'top_outflow':df.nsmallest(10,'主力净流入').to_dict('records')}
        _set_api_cache('moneyflow', result)
        return jsonify(result)
    except Exception as e:
        print(f"moneyflow error: {e}")
        return jsonify({'date':'','top_inflow':[],'top_outflow':[]})

@app.route('/api/heatmap', methods=['GET'])
def heatmap():
    cached = _cached_api('heatmap', 120)
    if cached: return jsonify(cached)
    try:
        today = datetime.date.today(); wd = today.weekday()
        if wd>=5: today = today-datetime.timedelta(days=wd-4)
        df = ak.stock_fund_flow_industry()
        df = df.rename(columns={'行业': '板块名称', '行业-涨跌幅': '涨跌幅', '净额': '主力净流入'})
        df['涨跌幅'] = pd.to_numeric(df['涨跌幅'], errors='coerce')
        df = df.dropna(subset=['涨跌幅']).sort_values('涨跌幅', ascending=False)
        top20 = df.head(20); bottom5 = df.tail(5)
        result = {'date':str(today),'data':pd.concat([top20,bottom5]).drop_duplicates().to_dict('records')}
        _set_api_cache('heatmap', result)
        return jsonify(result)
    except Exception as e:
        print(f"heatmap error: {e}")
        return jsonify({'date':'','data':[]})

@app.route('/api/sector_rotation', methods=['GET'])
def sector_rotation():
    try:
        df = ak.stock_fund_flow_industry()
        df = df.rename(columns={'行业': '板块名称', '净额': '主力净流入'})
        df['涨跌幅'] = pd.to_numeric(df['行业-涨跌幅'], errors='coerce')
        df['主力净流入'] = pd.to_numeric(df['主力净流入'], errors='coerce')
        top_in = df.nlargest(5,'主力净流入')[['板块名称','主力净流入']].to_dict('records')
        return jsonify({'top_inflow':top_in,'advice':'建议关注主力资金持续流入的板块'})
    except Exception as e:
        print(f"sector_rotation error: {e}")
        return jsonify({'error':'数据获取失败'})

@app.route('/api/market_ticker', methods=['GET'])
def market_ticker():
    """实时大盘指数（新浪源 + 腾讯fallback）"""
    cached = _cached_api('market_ticker', 10)
    if cached: return jsonify(cached)
    try:
        df = ak.stock_zh_index_spot_sina()
        result = {}
        target_codes = ['sh000001', 'sz399001', 'sz399006', 'sh000300', 'sh000688']
        target_names = {'sh000001': '上证指数', 'sz399001': '深证成指', 'sz399006': '创业板指', 'sh000300': '沪深300', 'sh000688': '科创50'}
        for _, row in df.iterrows():
            code = str(row['代码'])
            if code in target_codes:
                price = float(row['最新价'])
                change_pct = float(row['涨跌幅'])
                result[code] = {"name": target_names.get(code, str(row['名称'])), "price": round(price, 2), "change_pct": round(change_pct, 2)}
        _set_api_cache('market_ticker', result)
        return jsonify(result)
    except Exception as e:
        print(f"market_ticker error: {e}")
        return jsonify({})


@app.route('/api/market_env', methods=['GET'])
def market_env_route():
    env = market_environment()
    return jsonify({'environment':env,'advice':{'bull':'强势市场，可加大仓位','bear':'弱势市场，建议轻仓','normal':'震荡市场，控制仓位'}.get(env,'')})

@app.route('/api/data_quality', methods=['GET'])
def data_quality():
    issues = []
    try:
        df = _get_pool_snapshot()
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
    # 分数排名归一化
    _sig_vals = [c['signal'] for c in candidates if isinstance(c.get('signal'), (int, float))]
    if _sig_vals:
        _min_s, _max_s = min(_sig_vals), max(_sig_vals)
        if _max_s > _min_s:
            for c in candidates:
                c['signal'] = int((c['signal'] - _min_s) / (_max_s - _min_s) * 99 + 1)
        else:
            for c in candidates:
                c['signal'] = 50
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

@app.route('/api/factor_learning', methods=['GET'])
def factor_learning():
    """返回因子学习状态：策略因子 + 全部基础因子"""
    try:
        strategy_stats = load_json(FACTOR_STATS_FILE, {})
        strategy_names = {
            'ma_cross': '均线金叉', 'chan_theory': '缠论底分型', 'wave_theory': '波浪理论',
            'bull_trend': '多头趋势', 'hot_topic': '热点题材', 'event_driven': '事件驱动',
            'growth_quality': '成长质量', 'revaluation': '预期重估',
            'dragon_rising': '蛟龙出海', 'mountain_climb': '上山爬坡', 'resid_z': '统计超卖',
            'boll_squeeze': '布林突破', 'volume_price': '量价配合',
            'golden_cross_triple': '三金叉共振', 'oversold_reversal': '超跌反转',
            'momentum_breakout': '动量突破', 'low_vol_breakout': '低波突破',
            'consecutive_yang': '连阳蓄势'
        }
        factors = []
        # 1. 策略因子
        for key, name in strategy_names.items():
            ss = strategy_stats.get(key, {})
            t = ss.get('total', 0)
            w = ss.get('wins', 0)
            factors.append({
                'key': key, 'name': name, 'type': '策略',
                'total_trades': t,
                'wins': round(w, 1),
                'win_rate': round(w / t * 100, 1) if t > 0 else 0,
                'weight': ss.get('weight', 1.0),
                'total_profit': ss.get('total_profit', 0),
                'last_trade_pnl': ss.get('last_trade_pnl', 0),
                'boost': ss.get('boost', 1.0),
                'ic': ss.get('ic', 0),
            })
        # 2. 基础因子（从 FACTOR_REGISTRY）
        for key, info in FACTOR_REGISTRY.items():
            factors.append({
                'key': key, 'name': info.get('description', key), 'type': info.get('category', '基础'),
                'total_trades': 0, 'wins': 0, 'win_rate': 0,
                'weight': info.get('weight', 1.0),
                'total_profit': 0, 'last_trade_pnl': 0, 'boost': 1.0,
                'ic': info.get('ic_30d', 0),
            })
        factors.sort(key=lambda x: x['weight'], reverse=True)

        total_trades = len(load_trades())
        last_update = WEIGHT_LAST_UPDATE.strftime('%Y-%m-%d %H:%M:%S') if WEIGHT_LAST_UPDATE else '尚未更新'

        return jsonify({
            'factors': factors,
            'total_trades': total_trades,
            'last_update': last_update
        })
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

        fig, ax = plt.subplots(figsize=(14, 7), facecolor='#FFFFFF')
        ax.set_facecolor('#FFFFFF')

        width = 0.6
        for i in range(len(closes)):
            color = '#F44336' if closes[i] >= opens[i] else '#26A65B'
            body_bottom = min(opens[i], closes[i])
            body_height = abs(closes[i] - opens[i])
            if body_height < 0.001: body_height = 0.001
            ax.add_patch(Rectangle((i - width/2, body_bottom), width, body_height,
                                   facecolor=color, edgecolor=color, linewidth=0.5))
            ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=0.8)

        ax.plot(range(len(closes)), ma5, color='#f0b90b', linewidth=1.2, label='MA5')
        ax.plot(range(len(closes)), ma10, color='#5b8cce', linewidth=1.2, label='MA10')
        ax.plot(range(len(closes)), ma20, color='#F44336', linewidth=1.2, label='MA20')

        for i in range(1, len(closes)):
            if all(not np.isnan(m[i]) and not np.isnan(m[i-1]) for m in [ma5, ma10]):
                if ma5[i-1] <= ma10[i-1] and ma5[i] > ma10[i]:
                    ax.annotate('金叉', (i, lows[i] - (highs[i]-lows[i])*0.3),
                                fontsize=8, color='#f0b90b', ha='center',
                                bbox=dict(boxstyle='round,pad=0.2', facecolor='#F8F9FA', edgecolor='#f0b90b', alpha=0.9))

        for i in range(20, len(closes)):
            if closes[i] >= opens[i]:
                if closes[i-1] <= ma5[i-1] and closes[i-1] <= ma10[i-1] and closes[i-1] <= ma20[i-1]:
                    if closes[i] > ma5[i] and closes[i] > ma10[i] and closes[i] > ma20[i]:
                        ax.annotate('蛟龙\n出海', (i, highs[i] + (highs[i]-lows[i])*0.2),
                                    fontsize=9, color='#F44336', ha='center', weight='bold',
                                    bbox=dict(boxstyle='round,pad=0.3', facecolor='#FFF0F0', edgecolor='#F44336', alpha=0.9))

        ax.set_xticks(range(0, len(dates), max(1, len(dates)//8)))
        ax.set_xticklabels([dates[i] for i in range(0, len(dates), max(1, len(dates)//8))], color='#4B5563', fontsize=9)
        ax.tick_params(axis='y', colors='#4B5563')
        ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#E0E3EB'); ax.spines['bottom'].set_color('#E0E3EB')
        ax.grid(axis='y', color='#E4E7EB', linewidth=0.5)
        ax.legend(loc='upper left', fontsize=9, facecolor='#FFFFFF', edgecolor='#E0E3EB', labelcolor='#4B5563')
        ax.set_title(f'{code} 近30日K线图', color='#1A1A1A', fontsize=14, fontweight='bold', pad=15)

        buf = BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png', dpi=100, facecolor='#FFFFFF', bbox_inches='tight')
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



# ========== Ruflo API ==========

@app.route('/api/ruflo/status', methods=['GET'])
def ruflo_status():
    try:
        from mcp_client import check_status
        return jsonify(check_status())
    except Exception:
        return jsonify({'available': False})

@app.route('/api/ruflo/analyze', methods=['POST'])
def ruflo_analyze():
    try:
        from mcp_client import check_status, agent_spawn
        st = check_status()
        if not st.get('available'):
            return jsonify(st)
        data = request.get_json() or {}
        code = data.get('code', '')
        if not code:
            return jsonify({'error': 'no code'}), 400
        df = get_stock_daily_cached(code, 60)
        if df is None or len(df) < 20:
            return jsonify({'error': 'no data'}), 404
        info = {'code': code, 'close': [round(x,2) for x in df['close'].tolist()[-20:]]}
        result = agent_spawn('quant', f'Analyze {json.dumps(info)}')
        return jsonify({'result': result, 'available': True})
    except Exception as e:
        return jsonify({'available': False, 'error': str(e)})

@app.route('/api/ruflo/memory/store', methods=['POST'])
def ruflo_memory_store():
    try:
        from mcp_client import safe_store
        data = request.get_json() or {}
        text = data.get('text', '')
        if not text:
            return jsonify({'error': 'no text'}), 400
        meta = data.get('metadata', {})
        meta['timestamp'] = datetime.datetime.now().isoformat()
        result = safe_store(text, meta)
        return jsonify({'result': result, 'available': True})
    except Exception as e:
        return jsonify({'available': False, 'error': str(e)})

@app.route('/api/ruflo/memory/search', methods=['POST'])
def ruflo_memory_search():
    try:
        from mcp_client import safe_search
        data = request.get_json() or {}
        query = data.get('query', '')
        top_k = data.get('top_k', 5)
        if not query:
            return jsonify({'error': 'no query'}), 400
        result = safe_search(query, top_k)
        return jsonify({'result': result, 'available': True})
    except Exception as e:
        return jsonify({'available': False, 'error': str(e)})

@app.route('/api/ruflo/committee', methods=['POST'])
def ruflo_committee():
    try:
        from mcp_client import check_status, swarm_init
        st = check_status()
        if not st.get('available'):
            return jsonify(st)
        data = request.get_json() or {}
        stocks = data.get('stocks', [])
        market = data.get('market', {})
        if not stocks:
            return jsonify({'error': 'no stocks'}), 400
        result = swarm_init(
            ['quant_1','quant_2','quant_3'],
            f'Eval: {json.dumps(stocks, ensure_ascii=False)}',
            json.dumps(market, ensure_ascii=False)
        )
        return jsonify({'result': result, 'available': True})
    except Exception as e:
        return jsonify({'available': False, 'error': str(e)})

# ==================== OpenClaw微信推送 ====================

@app.route('/api/push-wechat', methods=['POST'])
def push_wechat():
    """HTTP推送微信：接收信号文本 → 调用openclaw推送微信"""
    token = request.headers.get('X-Deploy-Token', '')
    if token != DEPLOY_TOKEN:
        return jsonify({'error': '令牌错误'}), 403

    data = request.get_json(silent=True)
    if not data or 'message' not in data:
        return jsonify({'error': '缺少message字段'}), 400

    message = data['message']

    # 写入 planet_post_today.txt（兼容旧cron机制）
    try:
        post_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'planet_post_today.txt')
        os.makedirs(os.path.dirname(post_file), exist_ok=True)
        with open(post_file, 'w', encoding='utf-8') as f:
            f.write(message + '\n')
    except Exception as e:
        app.logger.warning(f'写入planet_post失败: {e}')

    # 调用 openclaw CLI 推送微信
    pushed = False
    push_error = ''
    try:
        r = subprocess.run(
            ['/usr/bin/openclaw', 'message', 'send',
             '--channel', 'openclaw-weixin',
             '--target', 'o9cq806Txs-nk16DWQsEQP347mY4@im.wechat',
             '--message', message],
            timeout=15, capture_output=True, text=True
        )
        pushed = r.returncode == 0
        if not pushed:
            push_error = (r.stderr or r.stdout or '')[:200]
            app.logger.warning(f'openclaw推送失败: {push_error}')
    except FileNotFoundError:
        push_error = 'openclaw CLI不存在'
        app.logger.info(push_error)
    except Exception as e:
        push_error = str(e)[:200]
        app.logger.warning(f'openclaw异常: {push_error}')

    return jsonify({'ok': True, 'pushed': pushed, 'push_error': push_error})


# ==================== HTTP部署接口 ====================

@app.route('/api/deploy', methods=['POST'])
def deploy_api():
    """HTTP部署：接收.py文件或tar.gz归档上传 → 写文件 → 可选重启/命令
    用法:
      单文件: curl -X POST -H "X-Deploy-Token: po2024" -F "file=@engine.py" http://host/api/deploy
      tar.gz: curl -X POST -H "X-Deploy-Token: po2024" -F "archive=@deploy.tar.gz" -F "command=nohup python3 -u server/monitor.py > logs/monitor.log 2>&1 &" http://host/api/deploy
    """
    token = request.headers.get('X-Deploy-Token', '')
    if token != DEPLOY_TOKEN:
        return jsonify({'error': '令牌错误'}), 403

    import subprocess
    import tarfile
    import io

    # 处理tar.gz归档上传
    if 'archive' in request.files:
        arch = request.files['archive']
        base_dir = '/opt/quant_pulse/'
        extracted = []
        has_core = False
        try:
            raw = arch.read()
            tar = tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz')
            extracted = [m.name for m in tar.getmembers()]  # 在close之前获取
            tar.extractall(path=base_dir)
            tar.close()
            has_core = any(f in ('app.py', 'engine.py', 'config.py') for f in extracted)
        except Exception as e:
            return jsonify({'error': f'解压失败: {e}'}), 500

        # 执行post-deploy命令
        cmd = request.form.get('command', '')
        cmd_out, cmd_err = '', ''
        if cmd:
            try:
                r = subprocess.run(['bash', '-c', cmd], capture_output=True, text=True, timeout=30, cwd=base_dir)
                cmd_out, cmd_err = r.stdout[-300:], r.stderr[-300:]
            except subprocess.TimeoutExpired:
                cmd_err = '命令超时(30s)'
            except Exception as e:
                cmd_err = str(e)

        # 重启主服务（如果上传的文件中包含app.py/engine.py等）
        restart_msg = ''
        if has_core or request.form.get('restart', '0') == '1':
            try:
                r = subprocess.run(['systemctl', 'restart', 'quant_pulse'], capture_output=True, text=True, timeout=15)
                restart_msg = '服务已重启'
            except subprocess.TimeoutExpired:
                restart_msg = '重启命令已发送（超时）'
            except FileNotFoundError:
                restart_msg = '非systemd环境'
            except Exception as e:
                restart_msg = f'重启失败: {e}'

        return jsonify({
            'ok': True,
            'message': f'归档已部署({len(extracted)}个文件)',
            'extracted': extracted,
            'restart': restart_msg,
            'command_stdout': cmd_out[-500:] if cmd_out else '',
            'command_stderr': cmd_err[-500:] if cmd_err else ''
        })

    # 处理单文件上传（原有逻辑 + 扩展文件类型）
    if 'file' not in request.files:
        return jsonify({'error': '缺少file字段或archive字段'}), 400

    f = request.files['file']
    filename = f.filename or ''
    allowed = ['engine.py', 'app.py', 'config.py', 'ai_service.py',
               '_sector_heat.py', 'data.py', 'scraper.py', 'wechat_bot.py']

    if filename not in allowed:
        return jsonify({'error': f'不允许的文件: {filename}，接受: {allowed}'}), 400

    deploy_path = f'/opt/quant_pulse/{filename}'
    try:
        f.save(deploy_path)
    except Exception as e:
        return jsonify({'error': f'写入失败: {e}'}), 500

    msg = f'{filename}已更新'
    # 核心文件改完重启
    if filename in ('app.py', 'engine.py', 'config.py'):
        try:
            r = subprocess.run(['systemctl', 'restart', 'quant_pulse'], capture_output=True, text=True, timeout=15)
            msg += '，服务已重启'
            return jsonify({'ok': True, 'message': msg, 'stdout': r.stdout[-200:] if r.stdout else '', 'stderr': r.stderr[-200:] if r.stderr else ''})
        except subprocess.TimeoutExpired:
            return jsonify({'ok': True, 'message': f'{msg}，重启命令已发送（超时）'})
        except FileNotFoundError:
            return jsonify({'ok': True, 'message': f'{msg}（非systemd环境，请手动重启）'})
        except Exception as e:
            return jsonify({'error': f'重启失败: {e}'}), 500

    return jsonify({'ok': True, 'message': msg})



@app.route('/api/alpha/signals')
def api_alpha_signals():
    """AI模型信号"""
    import pickle, os
    from config import DATA_DIR
    model_path = os.path.join(DATA_DIR, 'alpha_model.pkl')
    if os.path.exists(model_path):
        with open(model_path, 'rb') as f:
            model_data = pickle.load(f)
        from alpha_factory import fetch_lhb, fetch_zt_pool, build_features, predict_today
        lhb = fetch_lhb(10)
        zt = fetch_zt_pool()
        features = build_features(lhb, zt)
        signals = predict_today(model_data, features)
        return jsonify({'signals': signals, 'count': len(signals), 'accuracy': model_data.get('accuracy', 0)})
    return jsonify({'signals': [], 'count': 0, 'error': '模型未训练'})

@app.route('/api/alpha/train')
def api_alpha_train():
    """训练模型"""
    result = run_pipeline(90)
    if 'error' in result:
        return jsonify({'status': 'error', 'msg': result['error']})
    m = result['model']
    return jsonify({
        'status': 'ok',
        'accuracy': m.get('accuracy', 0),
        'n_train': m.get('n_train', 0),
        'signals': result.get('signals', [])[:5],
    })



# ==================== 橙卫AI 看板 ====================

@app.route('/og/dashboard')
def og_dashboard():
    """橙卫AI 绩效看板"""
    # 读取模拟盘记录
    sim_path = os.path.join(DATA_DIR, 'og_sim_log.jsonl')
    trades = []
    if os.path.exists(sim_path):
        with open(sim_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
    
    completed = [t for t in trades if t.get('pnl') is not None]
    stats = {
        'total': len(trades),
        'completed': len(completed),
        'win_rate': 0,
        'total_pnl': 0,
        'max_drawdown': 0,
    }
    OG_CAPITAL = 10000
    if completed:
        pnls = [t['pnl'] for t in completed]
        stats['win_rate'] = round(sum(1 for p in pnls if p>0)/len(pnls)*100, 1)
        stats['total_pnl'] = round(sum(pnls), 2)
        equity = [OG_CAPITAL]
        for p in pnls:
            equity.append(equity[-1] + p)
        eq = pd.Series(equity[1:])
        dd = (eq / eq.cummax() - 1).min() * 100
        stats['max_drawdown'] = round(dd, 2)
        stats['final_equity'] = round(equity[-1], 2)
    else:
        stats['final_equity'] = OG_CAPITAL
    
    # 今日信号
    today_signals = [t for t in trades if t.get('entry_price') is None]
    
    return jsonify({
        'stats': stats,
        'today_signals': today_signals[:5],
        'recent_trades': sorted(completed, key=lambda x: x.get('date',''), reverse=True)[:20],
    })

@app.route('/og/signals')
def og_signals():
    """今日信号 (纯文本, 适合微信)"""
    from scripts.daily_og import fetch_signals, load_model
    model_data = load_model()
    signals = fetch_signals(model_data)
    if signals:
        lines = [f"{s['code']} {s['name']} {s['proba']:.0%} 净买{s['nb_ratio']}%" for s in signals]
        return jsonify({'signals': signals, 'text': chr(10).join(lines)})
    return jsonify({'signals': [], 'text': '今日无信号'})

# ==================== LHB龙虎榜跟庄 ====================

@app.route('/api/lhb/signals')
def api_lhb_signals():
    """获取龙虎榜信号"""
    signals = get_today_signals()
    if not signals:
        df = fetch_lhb_data(3)
        if df is not None:
            signals = generate_signals(df)
    return jsonify({'signals': signals[:10], 'count': len(signals)})

@app.route('/api/lhb/refresh')
def api_lhb_refresh():
    """刷新龙虎榜数据"""
    try:
        signals = save_daily_signals()
        return jsonify({'status': 'ok', 'count': len(signals)})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)})

@app.route('/api/lhb/backtest')
def api_lhb_backtest():
    """龙虎榜回测结果"""
    from lhb_strategy import verify_backtest
    result = verify_backtest(60)
    return jsonify(result)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
