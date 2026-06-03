#!/usr/bin/env python3
"""
Pulse Orange 实时监控守护进程
策略：D1放量大阳观察池 → D2低开回踩确认 → 买入
大盘过滤 + 15分钟K线确认 + 动态止盈
"""
import sys, os, json, time, logging, traceback, re
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pandas as pd
import numpy as np

DATA_DIR = Path(__file__).parent.parent / 'data'
SIGNAL_FILE = DATA_DIR / 'signal_alert.json'
POOL_CACHE_FILE = DATA_DIR / 'candidate_pool.json'
WATCH_FILE = DATA_DIR / 'd1_watch_pool.json'
POSITIONS_FILE = DATA_DIR / 'positions.json'
SELL_LOG_FILE = DATA_DIR / 'signal_alert_sell.json'
MAX_SELL_LOG = 100
CHECK_INTERVAL = 10
TENCENT_BATCH_URL = 'http://qt.gtimg.cn/q='

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('monitor')


class Monitor:
    def __init__(self):
        self.pool = []
        self.positions = {}
        self.last_quotes = {}
        self.http = httpx.Client(timeout=5)
        self.running = True
        self._pool_mtime = 0
        self.watch_pool = []
        self.d1_cache = {}
        self.market_up = True  # 大盘过滤
        self._d2_qualified = set()  # 已通过15分K线确认的股票池
        self._load_positions()

    # ── 候选池 ──

    def load_pool(self):
        if POOL_CACHE_FILE.exists():
            with open(POOL_CACHE_FILE) as f:
                raw = json.load(f)
            if raw and isinstance(raw[0], dict):
                self.pool = [(p['code'], p.get('name', '')) for p in raw]
            else:
                self.pool = raw
            log.info(f'候选池: {len(self.pool)}只')
        else:
            self.pool = [('000001', '平安银行'), ('000002', '万科A')]

    def load_watch_pool(self):
        """加载D1观察池"""
        if WATCH_FILE.exists():
            with open(WATCH_FILE) as f:
                raw = json.load(f)
            self.watch_pool = raw if isinstance(raw, list) else []
            self.d1_cache = {p['code']: p for p in self.watch_pool}
            log.info(f'D1观察池: {len(self.watch_pool)}只')
        else:
            self.watch_pool = []
            self.d1_cache = {}

    # ── 持仓持久化 ──

    def _load_positions(self):
        """启动时从文件恢复持仓"""
        if POSITIONS_FILE.exists():
            try:
                raw = json.loads(POSITIONS_FILE.read_text(encoding='utf-8'))
                if isinstance(raw, list):
                    for p in raw:
                        code = p.get('code', '')
                        if code:
                            self.positions[code] = p
                elif isinstance(raw, dict):
                    self.positions = raw
                log.info(f'恢复持仓: {len(self.positions)}只')
            except Exception as e:
                log.warning(f'持仓文件读取失败: {e}')
                self.positions = {}
        else:
            self.positions = {}

    def _save_positions(self):
        """持仓持久化到文件"""
        try:
            data = list(self.positions.values())
            POSITIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            log.error(f'持仓保存失败: {e}')

    def _clean_sell_log(self):
        """卖出日志只保留最近MAX_SELL_LOG条"""
        try:
            if SELL_LOG_FILE.exists():
                raw = json.loads(SELL_LOG_FILE.read_text(encoding='utf-8'))
                sells = raw.get('sells', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                if len(sells) > MAX_SELL_LOG:
                    sells = sells[-MAX_SELL_LOG:]
                    SELL_LOG_FILE.write_text(
                        json.dumps({'sells': sells}, ensure_ascii=False, indent=2),
                        encoding='utf-8')
        except:
            pass

    def check_market(self):
        """大盘环境过滤：上证指数涨跌"""
        try:
            # 直接用上证指数判断
            url = TENCENT_BATCH_URL + 'sh000001'
            r = self.http.get(url, timeout=5)
            if r.status_code == 200:
                parts = r.text.split('~')
                if len(parts) > 4:
                    price = float(parts[3]) if parts[3] else 0
                    prev = float(parts[4]) if parts[4] else 0
                    if prev > 0:
                        idx_chg = (price - prev) / prev * 100
                        self.market_up = idx_chg > -0.5
                        return
            # 备选：用池中股票平均涨跌
            quotes = self.fetch_quotes()
            if quotes:
                changes = [q['change_pct'] for q in quotes.values() if q.get('change_pct') is not None]
                if changes:
                    avg = sum(changes) / len(changes)
                    self.market_up = avg > -0.3
        except:
            pass

    # ── 实时报价 ──

    def fetch_quotes(self):
        codes = [c[0] for c in self.pool] + [p['code'] for p in self.watch_pool]
        if not codes:
            return {}
        quotes = {}
        for i in range(0, len(codes), 50):
            batch = codes[i:i+50]
            prefixes = []
            for c in batch:
                p = 'sz' if not c.startswith(('6','9')) else 'sh'
                prefixes.append(f'{p}{c}')
            url = TENCENT_BATCH_URL + ','.join(prefixes)
            try:
                r = self.http.get(url, timeout=5)
                if r.status_code == 200:
                    for line in r.text.strip().split(';'):
                        if not line or '~' not in line:
                            continue
                        parts = line.split('~')
                        if len(parts) < 40:
                            continue
                        code = parts[2] if len(parts) > 2 else ''
                        name = parts[1] if len(parts) > 1 else ''
                        price = float(parts[3]) if parts[3] else 0
                        prev_close = float(parts[4]) if parts[4] else 0
                        open_p = float(parts[5]) if parts[5] else 0
                        volume = float(parts[6]) if parts[6] else 0
                        high = float(parts[33]) if parts[33] else 0
                        low = float(parts[34]) if parts[34] else 0
                        change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                        quotes[code] = {
                            'code': code, 'name': name, 'price': price,
                            'prev_close': prev_close, 'open': open_p,
                            'high': high, 'low': low, 'volume': volume,
                            'change_pct': change_pct,
                        }
            except:
                pass
        return quotes

    # ── D2买入条件检测 ──

    def check_d2_confirmation(self, code, quote):
        """D2确认：大盘>0 + 低开回踩D1开盘价 + 15分K线确认"""
        try:
            if not self.market_up:
                return False, None
            d1 = self.d1_cache.get(code)
            if not d1:
                return False, None
            d1_open = d1['d1_open']
            d1_close = d1['d1_close']
            d1_chg = abs(d1.get('d1_chg', 5))
            cur = quote['price']
            low = quote['low']

            # 今日必须低开/平开（不能高开太多）
            open_p = quote.get('open', cur)
            if open_p > d1_close * 1.02:
                return False, None  # 高开超2%不追

            # 条件1: 今日最低价必须回踩到D1开盘价+2%以内
            if low > d1_open * 1.03:
                return False, None  # 最低都没回踩到位

            # 条件2: 不能跌破D1开盘价太多（跌太多太弱，不是回踩）
            if low < d1_open * 0.97:
                return False, None  # 跌破D1开盘价3%以上，太弱

            # 条件3: 当前价必须在D1开盘价的2%以内（还没涨上去）
            if cur > d1_open * 1.02:
                return False, None  # 已经涨上去了，买点已过

            # 条件4: 已从最低点反弹（确认止跌）
            if cur < low * 1.005:
                return False, None  # 还没确认反弹

            # 条件3: 15分钟K线底部确认（只做一次，通过后加入白名单）
            # 注：Sina API可能被限流(456错误)，如无法获取则跳过K线确认
            if code not in self._d2_qualified:
                passed = False
                kline_available = False
                try:
                    from data import get_minute_kline
                    m15 = get_minute_kline(code, scale=15, bars=20)
                    if m15 is not None and len(m15) >= 5:
                        kline_available = True
                        today_str = datetime.now().strftime('%Y-%m-%d')
                        today15 = m15[m15['date'].str.startswith(today_str)]
                        if len(today15) >= 4:
                            min_idx = today15['low'].idxmin()
                            min_close = today15.loc[min_idx, 'close']
                            post_min = today15.loc[min_idx:]
                            latest_close = post_min['close'].iloc[-1]
                            # 从最低点反弹>0.5% 或 最近2根连续收高
                            if latest_close > min_close * 1.005:
                                passed = True
                            last2 = today15.tail(2)
                            if len(last2) == 2 and last2['close'].iloc[-1] > last2['close'].iloc[0]:
                                passed = True
                except:
                    pass
                if kline_available and not passed:
                    return False, None  # 有K线数据但没确认底部
                # 无K线数据（Sina限流）或已确认 → 通过
                self._d2_qualified.add(code)
                if kline_available:
                    log.info(f'{code} 15分K线确认通过')
                else:
                    log.info(f'{code} K线数据不可用(Sina限流)，跳过K线确认')

            return True, f'D2(昨{d1_chg:.0f}%放量,回踩{d1_open:.2f},现{cur})'
        except:
            return False, None

    def check_signals(self, code, quote):
        if code in self.positions:
            return None
        try:
            trig, det = self.check_d2_confirmation(code, quote)
            if trig:
                return [('D2确认买入', det)]
        except:
            pass
        return None

    # ── 推送（动态止盈）──

    def push_alert(self, code, quote, signals):
        for sig_name, sig_detail in signals:
            price = quote['price']
            d1 = self.d1_cache.get(code, {})
            d1_chg = abs(d1.get('d1_chg', 5))

            # 动态止盈：D1越强止盈越高
            if d1_chg >= 10:
                sl_pct, tp_pct = 0.93, 1.07  # -7%/+7%
            elif d1_chg >= 8:
                sl_pct, tp_pct = 0.95, 1.05  # -5%/+5%
            elif d1_chg >= 6:
                sl_pct, tp_pct = 0.97, 1.04  # -3%/+4%
            else:
                sl_pct, tp_pct = 0.98, 1.03  # -2%/+3%

            alert = {
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'code': code, 'name': quote.get('name', ''),
                'signal': sig_name, 'detail': sig_detail,
                'price': price,
                'stop_loss': round(price * sl_pct, 2),
                'target': round(price * tp_pct, 2),
                'd1_chg': d1_chg,
            }

            # 记录持仓
            self.positions[code] = {
                'code': code, 'name': quote.get('name', ''),
                'buy_price': price, 'buy_time': alert['time'],
                'buy_date': datetime.now().strftime('%Y-%m-%d'),
                'signal_type': sig_name, 'stop_loss': alert['stop_loss'],
                'target': alert['target'],
            }

            with open(SIGNAL_FILE, 'w') as f:
                json.dump(alert, f, ensure_ascii=False, indent=2)

            # 持仓落盘
            self._save_positions()

            # Hermes推送（写 planet_post_today.txt → Hermes读→推微信）
            try:
                post_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                          'data', 'planet_post_today.txt')
                post_lines = [
                    f'🚨 D2买入信号',
                    f'股票: {quote.get("name","")}({code})',
                    f'昨日D1: 涨{d1_chg}%',
                    f'买入价: {price}元',
                    f'止损: {alert["stop_loss"]}元 ({round((sl_pct-1)*100)}%)',
                    f'目标: {alert["target"]}元 (+{round((tp_pct-1)*100)}%)',
                    f'时间: {alert["time"]}',
                    '',
                    f'大盘环境: {"涨" if self.market_up else "跌"}',
                ]
                os.makedirs(os.path.dirname(post_file), exist_ok=True)
                with open(post_file, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(post_lines) + '\n')
            except:
                pass

            log.info(f'\n🚨 D2买入信号 {code} {quote.get("name","")} 价{price} '
                     f'止损{alert["stop_loss"]} 目标{alert["target"]}')

    # ── 持仓监控 ──

    def check_positions(self, quotes):
        to_remove = []
        sell_signals = []
        for code, pos in self.positions.items():
            if code not in quotes:
                continue
            q = quotes[code]
            cur_price = q['price']
            buy_price = pos['buy_price']
            pnl = (cur_price / buy_price - 1) * 100
            target = pos.get('target', buy_price * 1.03)
            stop = pos.get('stop_loss', buy_price * 0.97)

            # 持有天数计算
            try:
                buy_date = datetime.strptime(pos['buy_date'], '%Y-%m-%d')
                hold = (datetime.now() - buy_date).days
            except:
                hold = 0

            # 动态调整：T+1盈利多→移动止损到成本价
            if hold >= 1 and pnl > 3:
                stop = max(stop, buy_price * 1.00)  # 保本止损

            # 动态调整：T+2临近强制卖出→放宽止损防震仓
            if hold >= 1 and datetime.now().hour <= 10:
                # T+2早上如果还没触发，适当放宽止损
                stop = min(stop, buy_price * 0.96)

            reason = None
            if cur_price >= target:
                reason = f'止盈({pnl:+.1f}%)'
            elif cur_price <= stop:
                reason = f'止损({pnl:+.1f}%)'
            if not reason:
                try:
                    if hold >= 2 and datetime.now().hour >= 9 and datetime.now().weekday() < 5:
                        reason = f'强制卖出({pnl:+.1f}%)'
                except:
                    pass
            if reason:
                sell_signals.append({
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'code': code, 'name': pos.get('name', ''),
                    'reason': reason, 'buy_price': buy_price,
                    'sell_price': cur_price, 'pnl_pct': round(pnl, 2),
                })
                to_remove.append(code)

        for code in to_remove:
            del self.positions[code]

        if sell_signals:
            # 合并到历史卖出记录，只保留最近MAX_SELL_LOG条
            all_sells = []
            if SELL_LOG_FILE.exists():
                try:
                    old = json.loads(SELL_LOG_FILE.read_text(encoding='utf-8'))
                    old_sells = old.get('sells', []) if isinstance(old, dict) else (old if isinstance(old, list) else [])
                    all_sells.extend(old_sells)
                except:
                    pass
            all_sells.extend(sell_signals)
            if len(all_sells) > MAX_SELL_LOG:
                all_sells = all_sells[-MAX_SELL_LOG:]
            SELL_LOG_FILE.write_text(
                json.dumps({'sells': all_sells}, ensure_ascii=False, indent=2),
                encoding='utf-8')

            # 持仓落盘
            self._save_positions()

            for s in sell_signals:
                log.info(f'💰 卖出 {s["code"]} {s["reason"]} 盈亏{s["pnl_pct"]:+.1f}%')
        return sell_signals

    # ── 主循环 ──

    def run(self):
        log.info('='*50)
        log.info('Pulse Orange D1+D2监控启动')
        log.info(f'间隔{CHECK_INTERVAL}秒')
        self.load_pool()
        self.load_watch_pool()
        self.check_market()
        log.info(f'大盘环境: {"涨" if self.market_up else "跌"}')
        if self.watch_pool:
            sorted_pool = sorted(self.watch_pool, key=lambda x: -x.get('d1_chg', 0))
            for p in sorted_pool[:5]:
                log.info(f'  D1: {p.get("code","")} 昨涨{p.get("d1_chg","")}%')

        cycle = 0
        while self.running:
            try:
                cycle += 1

                # 非交易时段跳过检测
                now = datetime.now()
                if now.weekday() >= 5 or not (925 <= now.hour*100+now.minute <= 1500):
                    time.sleep(60)
                    continue

                quotes = self.fetch_quotes()

                # 定期刷新大盘和市场池
                if cycle % 60 == 0:
                    self.check_market()
                    if POOL_CACHE_FILE.exists():
                        new_mtime = os.path.getmtime(POOL_CACHE_FILE)
                        if new_mtime > self._pool_mtime:
                            self.load_pool()
                            self._pool_mtime = new_mtime

                # D1观察池→D2确认
                sorted_pool = sorted(self.watch_pool, key=lambda x: -x.get('d1_chg', 0))
                for entry in sorted_pool:
                    code = entry.get('code', '')
                    if not code or code in self.positions or code not in quotes:
                        continue
                    trig, det = self.check_d2_confirmation(code, quotes[code])
                    if trig:
                        self.push_alert(code, quotes[code], [('D2确认买入', det)])

                # 持仓监控
                self.check_positions(quotes)

                if cycle % 60 == 0:
                    log.info(f'[心跳] {cycle}轮 {len(self.watch_pool)}只观察 {len(self.positions)}只持仓')

                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                log.info('停止')
                break
            except Exception as e:
                log.error(f'异常: {e}')
                traceback.print_exc()
                time.sleep(30)


if __name__ == '__main__':
    m = Monitor()
    m.run()
