"""Pulse Orange v4 组合优化器 + 元层
从多策略结果中构建最优组合
"""
import sys, os, json, logging
from datetime import datetime
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR

logger = logging.getLogger('v4.portfolio')


def compute_correlation_matrix(results):
    """计算策略收益相关性矩阵

    Args:
        results: {strategy_name: {'result': {...}, 'metrics': {...}}}
    Returns:
        pd.DataFrame: 相关性矩阵
    """
    strategy_returns = {}
    for sname, data in results.items():
        daily = data.get('result', {}).get('daily', [])
        if len(daily) < 10:
            continue
        df_d = pd.DataFrame(daily)
        df_d['ret'] = df_d['total_value'].pct_change()
        strategy_returns[sname] = df_d.set_index('date')['ret']

    if not strategy_returns:
        return pd.DataFrame()

    combined = pd.DataFrame(strategy_returns)
    corr = combined.corr()
    return corr


def equal_weight_portfolio(metrics_dict):
    """等权组合"""
    survivors = {k: v for k, v in metrics_dict.items()
                 if v.get('total_trades', 0) >= 10 and v.get('sharpe', -10) > 0}
    if not survivors:
        return {}
    w = 1.0 / len(survivors)
    return {k: w for k in survivors}


def risk_parity_portfolio(metrics_dict, daily_df_dict):
    """风险平价：权重与波动率倒数成正比"""
    vols = {}
    for sname, df_d in daily_df_dict.items():
        if len(df_d) < 10:
            continue
        df_d['ret'] = df_d['total_value'].pct_change()
        vol = df_d['ret'].dropna().std()
        if vol > 0:
            vols[sname] = vol

    if not vols:
        return {}

    inv_vols = {k: 1.0 / v for k, v in vols.items()}
    total_inv = sum(inv_vols.values())
    return {k: v / total_inv for k, v in inv_vols.items()}


def kelly_portfolio(metrics_dict, max_weight=0.30):
    """凯利加权：权重与Sharpe/波动率成正比"""
    # 凯利分数 f* = (p*b - q)/b 简化版
    kelly_weights = {}
    for sname, m in metrics_dict.items():
        if m.get('total_trades', 0) < 20:
            continue
        wr = m.get('win_rate', 0) / 100.0
        avg_w = m.get('avg_win_pct', 0)
        avg_l = m.get('avg_loss_pct', 0)
        if avg_l <= 0:
            continue
        # 盈亏比
        b = avg_w / avg_l if avg_l > 0 else 1
        # 凯利公式
        f = (wr * b - (1 - wr)) / b if b > 0 else 0
        if f > 0:
            kelly_weights[sname] = min(f, max_weight)

    total = sum(kelly_weights.values())
    if total > 1:
        # 归一化
        kelly_weights = {k: v / total for k, v in kelly_weights.items()}

    return kelly_weights


def build_portfolio(metrics_dict, daily_df_dict, method='auto'):
    """构建最优组合

    Args:
        metrics_dict: {sname: metrics} 绩效指标
        daily_df_dict: {sname: DataFrame} 每日净值数据
        method: 'auto'|'equal'|'risk_parity'|'kelly'

    Returns:
        dict: {sname: weight}
    """
    survivors = {k: v for k, v in metrics_dict.items()
                 if v.get('total_trades', 0) >= 10 and v.get('sharpe', -10) > 0.5}

    n_survivors = len(survivors)
    if n_survivors == 0:
        logger.warning("无幸存策略")
        return {}
    if n_survivors == 1:
        sname = list(survivors.keys())[0]
        logger.info(f"单策略: {sname}")
        return {sname: 1.0}

    if method == 'auto':
        if n_survivors <= 2:
            method = 'equal'
        elif n_survivors <= 5:
            method = 'risk_parity'
        else:
            method = 'kelly'

    logger.info(f"组合方法: {method} ({n_survivors}个策略)")

    if method == 'equal':
        return equal_weight_portfolio(survivors)
    elif method == 'risk_parity':
        return risk_parity_portfolio(survivors, daily_df_dict)
    elif method == 'kelly':
        return kelly_portfolio(survivors)
    else:
        return equal_weight_portfolio(survivors)


def portfolio_backtest(weights, results):
    """对组合权重做回测（模拟组合表现）

    Args:
        weights: {sname: weight}
        results: {sname: {'result': {...}}}
    Returns:
        dict: 组合绩效
    """
    # 收集所有策略的每日收益
    all_returns = {}
    max_len = 0
    for sname, w in weights.items():
        daily = results.get(sname, {}).get('result', {}).get('daily', [])
        if not daily:
            continue
        df_d = pd.DataFrame(daily)
        df_d['ret'] = df_d['total_value'].pct_change()
        all_returns[sname] = df_d.set_index('date')['ret']
        max_len = max(max_len, len(df_d))

    if not all_returns or max_len < 10:
        return {'error': '数据不足'}

    # 对齐日期
    combined = pd.DataFrame(all_returns)
    combined = combined.dropna(how='all')

    if combined.empty or len(combined) < 10:
        return {'error': '对齐后数据不足'}

    # 组合收益 = Σ(w_i × r_i)
    portfolio_rets = pd.Series(0.0, index=combined.index)
    for sname, w in weights.items():
        if sname in combined.columns:
            portfolio_rets += w * combined[sname].fillna(0)

    # 组合净值
    portfolio_nav = (1 + portfolio_rets).cumprod()
    initial_nav = portfolio_nav.iloc[0] if len(portfolio_nav) > 0 else 1
    portfolio_nav = portfolio_nav / initial_nav

    # 计算绩效
    total_ret = (portfolio_nav.iloc[-1] - 1) * 100 if len(portfolio_nav) > 0 else 0

    # 年化收益
    n_days = len(portfolio_nav)
    annual_ret = (portfolio_nav.iloc[-1] ** (252 / n_days) - 1) * 100 if n_days > 0 else 0

    # 夏普
    excess = portfolio_rets.dropna() - 0.02 / 252
    sharpe = (np.sqrt(252) * excess.mean() / excess.std()
              if excess.std() > 0 else 0)

    # 最大回撤
    peak = portfolio_nav.cummax()
    dd = (portfolio_nav - peak) / peak * 100
    max_dd = dd.min()

    # 胜率(日级别)
    win_days = (portfolio_rets.dropna() > 0).mean() * 100

    return {
        'method': 'weighted_average',
        'total_return_pct': round(total_ret, 2),
        'annual_return_pct': round(annual_ret, 2),
        'sharpe': round(sharpe, 2),
        'max_drawdown': round(max_dd, 2),
        'win_days_pct': round(win_days, 1),
        'total_days': n_days,
        'n_strategies': len(weights),
        'weights': {k: round(v, 3) for k, v in weights.items()},
    }


# ====================================================================
# 元层 (Layer 4): 策略健康监控
# ====================================================================
class MetaLayer:
    """策略生命周期管理"""

    def __init__(self, state_file=None):
        self.state_file = state_file or os.path.join(DATA_DIR, 'v4_meta_state.json')
        self.strategy_states = {}  # sname -> {status, trades, sharpe_30d, ...}
        self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    self.strategy_states = json.load(f)
            except:
                self.strategy_states = {}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.strategy_states, f, indent=2)

    def update(self, strategy_name, new_trades, lookback=30):
        """更新策略状态"""
        if strategy_name not in self.strategy_states:
            self.strategy_states[strategy_name] = {
                'status': 'active',
                'total_trades': 0,
                'consecutive_losses': 0,
                'sharpe_recent': 0,
                'activation_date': str(datetime.now().date()),
                'last_sharpe_check': None,
            }

        state = self.strategy_states[strategy_name]

        if new_trades:
            df_t = pd.DataFrame(new_trades)
            # 最近N笔
            recent = df_t.tail(lookback) if len(df_t) > lookback else df_t

            wins = recent[recent['pnl_pct'] > 0]
            losses = recent[recent['pnl_pct'] <= 0]

            # 胜率
            wr = len(wins) / len(recent) * 100 if len(recent) > 0 else 0
            # 夏普
            sharp = (wr / 100 - 0.5) * np.sqrt(252)  # 简化

            # 连亏
            consec = 0
            max_consec = 0
            for _, t in df_t.iterrows():
                if t['pnl_pct'] <= 0:
                    consec += 1
                    max_consec = max(max_consec, consec)
                else:
                    consec = 0

            state['total_trades'] = len(df_t)
            state['win_rate_recent'] = round(wr, 1)
            state['sharpe_recent'] = round(sharp, 2)
            state['consecutive_losses'] = max_consec
            state['last_update'] = str(datetime.now().date())

            # ── 状态判定 ──
            if sharp < -0.5 or max_consec >= 7:
                state['status'] = 'paused'
                state['reason'] = f'Sharpe({sharp:.2f})过低或连亏{max_consec}笔'
            elif sharp < 0:
                state['status'] = 'caution'
                state['reason'] = f'Sharpe({sharp:.2f})为负,建议降权'
            else:
                state['status'] = 'active'
                state['reason'] = '正常'

        self._save_state()
        return state

    def get_decision(self, strategy_name):
        """获取当前对策略的决策"""
        state = self.strategy_states.get(strategy_name, {})
        status = state.get('status', 'active')
        if status == 'paused':
            return 'disabled', state.get('reason', '')
        elif status == 'caution':
            return 'reduce', state.get('reason', '')
        return 'normal', ''


def print_portfolio_report(portfolio_result, weights, corr_matrix=None):
    """打印组合报告"""
    print("\n" + "=" * 65)
    print("  Pulse Orange v4 组合报告")
    print("=" * 65)

    p = portfolio_result
    if 'error' in p:
        print(f"  ❌ {p['error']}")
        return

    print(f"  组合方法: {p['method']}")
    print(f"  策略数: {p['n_strategies']}")
    print(f"\n  ── 权重分布 ──")
    for sname, w in sorted(weights.items(), key=lambda x: -x[1]):
        print(f"    {sname:<25} {w*100:>5.1f}%")

    print(f"\n  ── 组合绩效 ──")
    print(f"    总收益: {p['total_return_pct']:+.2f}%")
    print(f"    年化收益: {p['annual_return_pct']:+.2f}%")
    print(f"    Sharpe: {p['sharpe']:.2f}")
    print(f"    最大回撤: {p['max_drawdown']:.1f}%")
    print(f"    日均胜率: {p['win_days_pct']:.1f}%")
    print(f"    回测天数: {p['total_days']}")

    if corr_matrix is not None and not corr_matrix.empty:
        print(f"\n  ── 策略相关性矩阵 ──")
        print(corr_matrix.to_string())

    print("=" * 65)
