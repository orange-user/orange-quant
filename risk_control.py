"""
Pulse Orange 风控模块
单日最大亏损 / 总资金回撤 / 黑名单
"""
import json
import os
import datetime

from config import ACCOUNT_CAPITAL, TRADES_FILE, load_json, save_json
from logger import get_logger

log = get_logger('risk')

# 风控参数
MAX_DAILY_LOSS = ACCOUNT_CAPITAL * 0.03   # 单日最大亏损 3%
MAX_TOTAL_DRAWDOWN = ACCOUNT_CAPITAL * 0.10  # 总回撤上限 10%
BLACKLIST_FILE = os.path.join(os.path.dirname(__file__), 'blacklist.json')


def load_blacklist():
    """加载黑名单"""
    return load_json(BLACKLIST_FILE, [])


def save_blacklist(codes):
    """保存黑名单"""
    save_json(codes, BLACKLIST_FILE)


def add_to_blacklist(code, reason=''):
    """加入黑名单"""
    blacklist = load_blacklist()
    entry = {'code': code, 'reason': reason, 'date': datetime.datetime.now().strftime('%Y-%m-%d')}
    if code not in [b['code'] for b in blacklist]:
        blacklist.append(entry)
        save_blacklist(blacklist)
        log.warning(f'{code} 加入黑名单: {reason}')


def check_blacklist(code):
    """检查是否在黑名单中"""
    for b in load_blacklist():
        if b['code'] == code:
            return True
    return False


def today_loss():
    """今日累计亏损"""
    trades = load_json(TRADES_FILE, [])
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    total = 0
    for t in trades:
        if t.get('sell_time', '').startswith(today):
            profit = t.get('profit', 0)
            if profit < 0:
                total += profit
    return abs(total)


def check_daily_loss():
    """检查单日亏损是否超限"""
    loss = today_loss()
    if loss > MAX_DAILY_LOSS:
        log.error(f'单日亏损 {loss:.1f} 超限 {MAX_DAILY_LOSS:.1f}, 停止交易')
        return False
    return True


def check_total_drawdown():
    """检查总回撤"""
    trades = load_json(TRADES_FILE, [])
    if not trades:
        return True

    total_profit = sum(t.get('profit', 0) for t in trades[-30:])
    if total_profit < 0 and abs(total_profit) > MAX_TOTAL_DRAWDOWN:
        log.error(f'总回撤 {abs(total_profit):.1f} 超限 {MAX_TOTAL_DRAWDOWN:.1f}')
        return False
    return True


def pre_trade_check(code):
    """交易前风控检查"""
    # 黑名单检查
    if check_blacklist(code):
        log.warning(f'{code} 在黑名单中, 禁止交易')
        return False

    # 单日亏损检查
    if not check_daily_loss():
        return False

    # 总回撤检查
    if not check_total_drawdown():
        return False

    return True
