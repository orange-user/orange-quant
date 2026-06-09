"""v4 策略基类 + 统一回测循环
纯OHLCV+成交量结构分析，零指标（无MA/RSI/MACD/KDJ/布林）
每个策略回答一个微观结构问题
"""
import sys, os, json, warnings, sqlite3, logging
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, DATA_DIR

warnings.filterwarnings('ignore')
logger = logging.getLogger('v4.base')

# ── 交易成本 ──
SLIPPAGE = 0.001          # 默认滑点 0.1%
COMM_RATE = 0.00025       # 佣金万2.5
MIN_COMM = 5.0            # 最低佣金5元
STAMP_RATE = 0.001        # 印花税千1（仅卖出）

def trade_cost(price, shares, is_buy):
    """计算交易成本"""
    value = price * shares
    comm = max(value * COMM_RATE, MIN_COMM)
    stamp = 0 if is_buy else value * STAMP_RATE
    return value + comm + stamp if is_buy else value - comm - stamp


# ── SQLite数据读取 ──
def read_sqlite_daily(code, min_days=60):
    """从SQLite读取日线数据"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT date, open, close, high, low, volume FROM daily_data "
            "WHERE code=? ORDER BY date ASC", conn, params=(code,))
        if df.empty or len(df) < min_days:
            return None
        for c in ['open','close','high','low','volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['open','close','high','low','volume'])
        if len(df) < min_days:
            return None
        return df
    except Exception:
        return None
    finally:
        conn.close()


def get_csi300_cache():
    """获取沪深300数据的缓存（用于市场环境判断）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT date, close FROM daily_data WHERE code='000300' ORDER BY date ASC", conn)
        if df.empty or len(df) < 20:
            # Try sh000300
            df = pd.read_sql_query(
                "SELECT date, close FROM daily_data WHERE code='sh000300' ORDER BY date ASC", conn)
        if df.empty or len(df) < 20:
            return None
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna()
        return df
    except Exception:
        return None
    finally:
        conn.close()


def get_eligible_codes(market_cap_min=0):
    """获取符合条件的股票代码池（排除ST/退市/科创板/北交所）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        db_codes = set(r[0] for r in conn.execute(
            "SELECT DISTINCT code FROM daily_data").fetchall())
    except:
        db_codes = set()
    finally:
        conn.close()

    eligible = [c for c in db_codes
                if c.startswith(('0','3','6'))
                and not c.startswith(('300','301','688','689','8','4'))]
    return sorted(eligible)


# ── 策略基类 ──
class Strategy:
    """策略基类
    子类实现:
      entry_signals(df, date_idx) -> bool   # 某日是否触发买入
      exit_signals(df, entry_idx, current_idx) -> (bool, str)  # (是否卖出, 原因)
    """
    name = "base"
    description = ""
    params = {}                # 可调参数
    param_grid = {}            # {param: [values]} 参数网格
    regime_allow = []          # 允许的环境列表 ['发酵','高潮','分歧','退潮','冰点']
    max_hold_days = 10         # 最大持仓天数
    stop_loss_pct = 0.0        # 止损比例 (0=不设)
    take_profit_pct = 0.0      # 止盈比例 (0=不设)

    def __init__(self, params=None):
        if params:
            self.params.update(params)
        self.trades = []
        self.daily_pnl = []

    def entry_signals(self, df: pd.DataFrame, date_idx: int) -> bool:
        """子类重写: 返回True表示买入"""
        raise NotImplementedError

    def exit_signals(self, df: pd.DataFrame, entry_idx: int,
                     current_idx: int) -> tuple:
        """子类重写: 返回 (should_sell: bool, reason: str)"""
        raise NotImplementedError

    def run_backtest(self, codes=None, start_idx=None, end_idx=None,
                     capital=100000, slippage=SLIPPAGE, max_positions=2):
        """运行回测
        Args:
            codes: 股票代码列表 (None=全市场)
            start_idx: 起始日索引 (None=最长数据的60%)
            end_idx: 结束日索引 (None=最长数据的95%)
            capital: 初始资金
            slippage: 滑点比例
            max_positions: 最大同时持仓数
        Returns:
            dict: 回测结果
        """
        if codes is None:
            codes = get_eligible_codes()

        # 构建时间轴：使用所有股票数据的并集日期
        all_dates = []
        code_data = {}
        logger.info(f"读取数据: {len(codes)}只股票")
        for code in codes:
            df = read_sqlite_daily(code, min_days=60)
            if df is not None:
                code_data[code] = df
                all_dates.extend(df['date'].tolist())

        if not code_data:
            return {'error': '无有效数据', 'trades': [], 'daily': []}

        all_dates = sorted(set(all_dates))
        if len(all_dates) < 60:
            return {'error': f'数据不足: {len(all_dates)}天', 'trades': [], 'daily': []}

        if start_idx is None:
            start_idx = int(len(all_dates) * 0.6)
        if end_idx is None:
            end_idx = int(len(all_dates) * 0.95)

        trading_dates = all_dates[start_idx:end_idx]
        logger.info(f"回测期间: {trading_dates[0]} ~ {trading_dates[-1]} ({len(trading_dates)}天)")

        # ── 回测主循环 ──
        cash = capital
        positions = []     # 当前持仓
        trades = []        # 已平仓交易
        daily_values = []  # 每日净值

        date_to_idx = {d: i for i, d in enumerate(all_dates)}

        for di, today in enumerate(trading_dates):
            today_str = str(today)[:10]
            today_positions_value = 0

            # ── 卖出 ──
            for pos in list(positions):
                code = pos['code']
                df = code_data.get(code)
                if df is None:
                    continue
                current_idx_in_df = df[df['date'] == today].index
                if len(current_idx_in_df) == 0:
                    continue
                ci = current_idx_in_df[0]
                entry_idx_in_df = pos['entry_df_idx']

                days_held = di - pos['buy_day_idx']
                if days_held >= self.max_hold_days:
                    # 强制卖出
                    row = df.loc[ci]
                    sell_price = float(row['close']) * (1 - slippage)
                    proceeds = trade_cost(sell_price, pos['shares'], False)
                    pnl = (proceeds - pos['cost_basis']) / pos['cost_basis'] * 100
                    trades.append({
                        'code': code, 'strategy': self.name,
                        'buy_date': pos['buy_date'], 'sell_date': today_str,
                        'buy_price': pos['buy_price'], 'sell_price': round(sell_price, 2),
                        'shares': pos['shares'], 'pnl_pct': round(pnl, 2),
                        'pnl_val': round(proceeds - pos['cost_basis'], 2),
                        'hold_days': days_held, 'reason': 'force_exit',
                    })
                    cash += proceeds
                    positions.remove(pos)
                    continue

                should_sell, reason = self.exit_signals(df, entry_idx_in_df, ci)
                if should_sell:
                    row = df.loc[ci]
                    sell_price = float(row['close']) * (1 - slippage)
                    proceeds = trade_cost(sell_price, pos['shares'], False)
                    pnl = (proceeds - pos['cost_basis']) / pos['cost_basis'] * 100
                    trades.append({
                        'code': code, 'strategy': self.name,
                        'buy_date': pos['buy_date'], 'sell_date': today_str,
                        'buy_price': pos['buy_price'], 'sell_price': round(sell_price, 2),
                        'shares': pos['shares'], 'pnl_pct': round(pnl, 2),
                        'pnl_val': round(proceeds - pos['cost_basis'], 2),
                        'hold_days': days_held, 'reason': reason,
                    })
                    cash += proceeds
                    positions.remove(pos)

            # ── 计算当前持仓市值 ──
            for pos in positions:
                df = code_data.get(pos['code'])
                if df is not None:
                    row = df[df['date'] == today]
                    if len(row) > 0:
                        cur_price = float(row.iloc[0]['close'])
                        pos['current_value'] = pos['shares'] * cur_price
                        today_positions_value += pos['current_value']

            total_value = cash + today_positions_value
            daily_values.append({
                'date': today_str, 'cash': round(cash, 2),
                'position_value': round(today_positions_value, 2),
                'total_value': round(total_value, 2),
                'positions': len(positions),
            })

            # ── 买入 (在每日循环结束前) ──
            if len(positions) >= max_positions:
                continue

            for code, df in code_data.items():
                if len(positions) >= max_positions:
                    break
                # 是否已持仓
                if any(p['code'] == code for p in positions):
                    continue

                ci = df[df['date'] == today].index
                if len(ci) == 0:
                    continue
                ci = ci[0]

                # 必须有足够的历史数据
                if ci < 30:
                    continue

                if not self.entry_signals(df, ci):
                    continue

                # 买入(次日开盘)
                next_idx = ci + 1
                if next_idx >= len(df):
                    continue
                next_row = df.iloc[next_idx]
                buy_price = float(next_row['open']) * (1 + slippage)
                shares_hands = max(1, int(cash * 0.5 / max_positions / (buy_price * 100)))
                shares = shares_hands * 100
                cost = trade_cost(buy_price, shares, True)

                if cost > cash:
                    # 调整手数
                    shares_hands = max(1, int(cash * 0.8 / (buy_price * 100)))
                    shares = shares_hands * 100
                    cost = trade_cost(buy_price, shares, True)
                    if cost > cash:
                        continue

                positions.append({
                    'code': code, 'shares': shares, 'buy_price': buy_price,
                    'cost_basis': cost, 'buy_date': str(df.iloc[next_idx]['date'])[:10],
                    'buy_day_idx': di, 'entry_df_idx': next_idx,
                    'current_value': shares * buy_price,
                })
                cash -= cost

        # ── 强制平仓 ──
        for pos in list(positions):
            code = pos['code']
            df = code_data.get(code)
            if df is not None and len(df) > 0:
                last_row = df.iloc[-1]
                sell_price = float(last_row['close']) * (1 - slippage)
                proceeds = trade_cost(sell_price, pos['shares'], False)
                pnl = (proceeds - pos['cost_basis']) / pos['cost_basis'] * 100
                trades.append({
                    'code': code, 'strategy': self.name,
                    'buy_date': pos['buy_date'],
                    'sell_date': str(last_row['date'])[:10],
                    'buy_price': pos['buy_price'],
                    'sell_price': round(sell_price, 2),
                    'shares': pos['shares'], 'pnl_pct': round(pnl, 2),
                    'pnl_val': round(proceeds - pos['cost_basis'], 2),
                    'hold_days': len(trading_dates) - pos['buy_day_idx'],
                    'reason': 'end_of_test',
                })
                cash += proceeds
            positions.remove(pos)

        logger.info(f"完成: {len(trades)}笔交易")

        return {
            'strategy': self.name,
            'description': self.description,
            'trades': trades,
            'daily': daily_values,
            'initial_capital': capital,
            'final_capital': round(cash, 2),
            'total_trades': len(trades),
        }


def compute_metrics(result):
    """从回测结果计算绩效指标"""
    trades = result.get('trades', [])
    daily = result.get('daily', [])
    if not trades:
        return {'error': '无交易', 'total_trades': 0}

    df_t = pd.DataFrame(trades)

    # 胜率
    wins = df_t[df_t['pnl_pct'] > 0]
    losses = df_t[df_t['pnl_pct'] <= 0]
    win_rate = len(wins) / len(df_t) * 100 if len(df_t) > 0 else 0

    # 盈亏
    avg_win = wins['pnl_pct'].mean() if len(wins) > 0 else 0
    avg_loss = abs(losses['pnl_pct'].mean()) if len(losses) > 0 else 0
    profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')
    total_return = df_t['pnl_val'].sum()

    # 最大回撤
    if daily:
        df_d = pd.DataFrame(daily)
        df_d['peak'] = df_d['total_value'].cummax()
        df_d['dd'] = (df_d['total_value'] - df_d['peak']) / df_d['peak'] * 100
        max_dd = df_d['dd'].min()
        total_pct = (df_d['total_value'].iloc[-1] / df_d['total_value'].iloc[0] - 1) * 100
    else:
        max_dd = 0
        total_pct = 0

    # 夏普
    if daily and len(daily) > 1:
        df_d = pd.DataFrame(daily)
        df_d['daily_ret'] = df_d['total_value'].pct_change()
        excess = df_d['daily_ret'].dropna() - 0.02/252
        sharpe = (np.sqrt(252) * excess.mean() / excess.std()
                  if excess.std() > 0 else 0)
    else:
        sharpe = 0

    # 平均持仓
    avg_hold = df_t['hold_days'].mean() if 'hold_days' in df_t.columns else 0

    # 最大连续亏损
    df_t['is_win'] = df_t['pnl_pct'] > 0
    max_consec_loss = 0
    curr_loss = 0
    for w in df_t['is_win']:
        if not w:
            curr_loss += 1
            max_consec_loss = max(max_consec_loss, curr_loss)
        else:
            curr_loss = 0

    return {
        'strategy': result.get('strategy', ''),
        'total_trades': len(trades),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2),
        'sharpe': round(sharpe, 2),
        'max_drawdown': round(max_dd, 2),
        'total_return_pct': round(total_pct, 2),
        'total_return_val': round(total_return, 2),
        'avg_hold_days': round(avg_hold, 1),
        'avg_win_pct': round(avg_win, 2),
        'avg_loss_pct': round(avg_loss, 2),
        'max_consec_loss': max_consec_loss,
        'winning_trades': int(len(wins)),
        'losing_trades': int(len(losses)),
    }


def print_metrics(metrics):
    """打印绩效报告"""
    if 'error' in metrics:
        print(f"  ❌ {metrics['error']}")
        return
    s = metrics
    print(f"  📊 {s['strategy']}")
    print(f"     交易: {s['total_trades']}笔 | "
          f"胜率: {s['win_rate']}% ({s['winning_trades']}/{s['total_trades']})")
    print(f"     总收益: {s['total_return_val']:+.0f} ({s['total_return_pct']:+.2f}%) | "
          f"Sharpe: {s['sharpe']:.2f}")
    print(f"     最大回撤: {s['max_drawdown']:.1f}% | "
          f"盈亏比: {s['profit_factor']:.2f}")
    print(f"     均持仓: {s['avg_hold_days']}天 | "
          f"均盈: {s['avg_win_pct']:+.2f}% 均亏: {s['avg_loss_pct']:.2f}%")
    print(f"     最大连亏: {s['max_consec_loss']}笔")
