"""跟踪止盈条件单：触及目标→激活跟踪→回落TRAIL_PCT%卖出
- 硬止损：基于D1涨幅（-2%到-7%）
- 跟踪止盈：触及目标后从峰值回落TRAIL_PCT%卖出
- 最多持有FORCE_SELL_DAYS天强制卖出"""
import logging
logger = logging.getLogger('backtest.sell')

TRAIL_PCT = 3.0
FORCE_SELL_DAYS = 5


def get_sl_tp(d1_chg):
    if d1_chg >= 10: return 0.93, 1.07
    elif d1_chg >= 8: return 0.95, 1.05
    elif d1_chg >= 6: return 0.97, 1.04
    else: return 0.98, 1.03


class SellResult:
    def __init__(self, should_sell, sell_price, reason):
        self.should_sell = should_sell
        self.sell_price = sell_price
        self.reason = reason


def evaluate_sell_conditions(position, open_price, high, low, close,
                             prev_close, days_since_buy, slippage=0.001):
    entry = position['buy_price']
    d1_chg = position.get('d1_chg', 5)

    sl_pct, tp_pct = get_sl_tp(d1_chg)
    sl_price = entry * sl_pct
    tp_price = entry * tp_pct

    def apply_slip(p):
        return p * (1 - slippage)

    # 硬止损：不假设最优成交价。跳空低开时按(止损+最低)/2成交
    if low <= sl_price:
        if abs(low - sl_price) / sl_price > 0.02:  # 跳空>2%，按中间价
            fill = (sl_price + low) / 2
        else:  # 正常触及止损
            fill = sl_price
        return SellResult(True, apply_slip(fill), f'止损')

    # 跟踪止盈
    trailing_active = position.get('trailing_active', False)
    trailing_peak = position.get('trailing_peak', entry)

    # 激活：日内高价触及目标
    if not trailing_active and high >= tp_price:
        position['trailing_active'] = True
        position['trailing_peak'] = high
        trailing_active = True
        trailing_peak = high

    if trailing_active:
        if high > trailing_peak:
            trailing_peak = high
            position['trailing_peak'] = trailing_peak
        sell_at = trailing_peak * (1 - TRAIL_PCT / 100)
        if low <= sell_at:
            # 跳空回落>2%按中间价，否则按触发价
            if abs(low - sell_at) / sell_at > 0.02:
                fill = (sell_at + low) / 2
            else:
                fill = sell_at
            return SellResult(True, apply_slip(fill), f'跟踪止盈')

    # 时间强制卖出
    if days_since_buy >= FORCE_SELL_DAYS:
        return SellResult(True, apply_slip(close), f'超{FORCE_SELL_DAYS}天强卖')

    return SellResult(False, None, '持有')


def evaluate_sell_for_position(position, today_data, days_since_buy):
    return evaluate_sell_conditions(
        position=position,
        open_price=float(today_data['open']),
        high=float(today_data['high']),
        low=float(today_data['low']),
        close=float(today_data['close']),
        prev_close=float(today_data.get('prev_close', today_data['close'])),
        days_since_buy=days_since_buy,
    )
