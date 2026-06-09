"""回测持仓管理——与实盘 positions.json 完全隔离"""
import logging
import numpy as np

logger = logging.getLogger('backtest.position')

# 交易成本参数
COMMISSION_RATE = 0.00025   # 佣金万2.5
STAMP_DUTY_RATE = 0.001     # 印花税千1（仅卖出）
TRADE_COST_MIN = 5.0        # 最低佣金5元


class BacktestPortfolio:
    """回测投资组合"""

    def __init__(self, initial_capital=100000):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions = []       # list[dict]
        self.trade_history = []   # list[dict] 已平仓交易
        self.daily_values = []    # list[dict] 每日净值快照

    def open_position(self, code, buy_price, shares_hands, signal,
                      buy_date, buy_day_idx, slippage=0.001):
        """开仓（含买入佣金+滑点）
        shares_hands: 手数（1手=100股）
        slippage: 滑点比例，买入按高价算
        """
        effective_price = buy_price * (1 + slippage)  # 买入滑点
        shares = shares_hands * 100
        cost_before = shares * effective_price
        commission = max(cost_before * COMMISSION_RATE, TRADE_COST_MIN)
        total_cost = cost_before + commission

        if total_cost > self.cash:
            # 资金不足
            max_shares = int((self.cash - TRADE_COST_MIN) /
                             (buy_price * (1 + COMMISSION_RATE)))
            max_shares = (max_shares // 100) * 100  # 整百
            if max_shares < 100:
                return None
            shares = max_shares
            shares_hands = max_shares // 100
            cost_before = shares * buy_price
            commission = max(cost_before * COMMISSION_RATE, TRADE_COST_MIN)
            total_cost = cost_before + commission

        position = {
            'code': code,
            'buy_price': round(effective_price, 2),
            'shares': shares,
            'shares_hands': shares_hands,
            'cost_before': cost_before,
            'commission': round(commission, 2),
            'total_cost': round(total_cost, 2),
            'signal': signal,
            'buy_date': buy_date,
            'buy_day_idx': buy_day_idx,
        }
        self.positions.append(position)
        self.cash -= total_cost
        logger.info(f"  开仓 {code}: {buy_price:.2f}×{shares_hands}手 "
                    f"({total_cost:.0f}元, signal={signal})")
        return position

    def close_position(self, code, sell_price, sell_date, reason='', slippage=0.001):
        """平仓（含卖出佣金+印花税+滑点）
        slippage: 滑点比例，卖出按低价算
        """
        for p in list(self.positions):
            if p['code'] == code:
                effective_price = sell_price * (1 - slippage)  # 卖出滑点
                proceeds_before = p['shares'] * effective_price
                commission = max(proceeds_before * COMMISSION_RATE, TRADE_COST_MIN)
                stamp_duty = proceeds_before * STAMP_DUTY_RATE
                total_proceeds = proceeds_before - commission - stamp_duty

                profit_val = total_proceeds - p['total_cost']
                profit_pct = (profit_val / p['total_cost']) * 100

                trade = {
                    'code': code,
                    'buy_price': p['buy_price'],
                    'sell_price': round(sell_price, 2),
                    'shares': p['shares'],
                    'buy_date': p['buy_date'],
                    'sell_date': sell_date,
                    'hold_days': None,  # 由runner计算
                    'signal': p['signal'],
                    'profit_val': round(profit_val, 2),
                    'profit_pct': round(profit_pct, 2),
                    'commission': round(p['commission'] + commission, 2),
                    'stamp_duty': round(stamp_duty, 2),
                    'sell_reason': reason,
                }
                self.trade_history.append(trade)
                self.cash += total_proceeds
                self.positions.remove(p)

                logger.info(f"  平仓 {code}: {sell_price:.2f}, "
                            f"盈亏{profit_pct:+.2f}%, 原因:{reason}")
                return trade

        return None

    def position_value(self, current_prices=None):
        """当前持仓市值
        current_prices: dict[code → 当日收盘价]，传入则用市价，否则按成本
        """
        if current_prices:
            total = 0
            for p in self.positions:
                price = current_prices.get(p['code'], p['buy_price'])
                total += p['shares'] * price
            return total
        return sum(p['total_cost'] for p in self.positions)

    def total_value(self, current_prices=None):
        """总资产 = 现金 + 持仓市值（市价，如果提供）"""
        mkt_value = self.position_value(current_prices)
        return self.cash + mkt_value

    def snapshot(self, date, current_prices=None):
        """记录每日快照（市价，如果提供）"""
        mkt_val = self.position_value(current_prices)
        val = self.cash + mkt_val
        self.daily_values.append({
            'date': date,
            'cash': round(self.cash, 2),
            'position_value': round(mkt_val, 2),
            'total_value': round(val, 2),
            'position_count': len(self.positions),
        })
        return val

    def summary(self):
        """返回当前状态摘要"""
        return {
            'initial_capital': self.initial_capital,
            'cash': round(self.cash, 2),
            'position_count': len(self.positions),
            'trade_count': len(self.trade_history),
            'total_value': round(self.total_value(), 2),
        }


def calc_backtest_kelly(price, signal, trade_history, capital):
    """回测版凯利公式——用回测自己的交易历史，不碰实盘 trades.json

    参数:
        price: 当前股价
        signal: 信号值 0-100
        trade_history: list[dict] 已平仓交易记录
        capital: 当前可用资金
    返回:
        dict: {kelly_pct, suggested_shares, suggested_value, can_afford}
    """
    # 从交易历史提取盈亏
    if len(trade_history) < 3:
        p, b = 0.55, 1.5  # 默认值
    else:
        profits = np.array([t.get('profit_pct', 0) for t in trade_history[-60:]])
        weights = np.exp(-np.arange(len(profits))[::-1] / 20.0)
        weights /= weights.sum()
        win_mask = profits > 0
        loss_mask = profits < 0
        w_sum = weights[win_mask].sum()
        l_sum = weights[loss_mask].sum()
        p = w_sum / (w_sum + l_sum) if (w_sum + l_sum) > 0 else 0.55
        avg_win = np.average(profits[win_mask], weights=weights[win_mask]) if win_mask.any() else 2.0
        avg_loss = abs(np.average(profits[loss_mask], weights=weights[loss_mask])) if loss_mask.any() else 2.0
        b = avg_win / max(avg_loss, 0.5)
        p = max(0.3, min(0.9, p))
        b = max(0.5, min(5.0, b))

    # 半凯利
    f_star = (p * (b + 1) - 1) / b if b > 0 else 0
    f_star = max(0, min(f_star, 0.5))
    kelly_pct = f_star * 0.5 * 100

    # 信号加成
    score_mult = 0.5 + (signal / 100.0)
    score_mult = max(0.3, min(2.0, score_mult))
    kelly_pct *= score_mult
    kelly_pct = max(5.0, min(50.0, kelly_pct))

    position_value = capital * kelly_pct / 100.0
    shares_raw = position_value / (price * 100) if price > 0 else 0

    if shares_raw < 1:
        suggested_shares = 1 if signal >= 60 else 0
    else:
        suggested_shares = max(1, int(round(shares_raw)))

    return {
        'kelly_pct': round(kelly_pct, 1),
        'suggested_shares': suggested_shares,
        'suggested_value': round(position_value, 2),
        'can_afford': suggested_shares > 0,
    }
