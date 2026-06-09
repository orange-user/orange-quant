"""过拟合检测：Walk-forward验证 + Monte Carlo模拟"""
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger('backtest.overfit')


def walk_forward_check(trades, train_ratio=0.7):
    """Walk-forward验证：前后分段对比

    返回:
        dict: {level, message, train_metrics, test_metrics, gap}
    """
    if not trades or len(trades) < 10:
        return {'level': 'unknown', 'message': '交易太少，无法判断过拟合'}

    df = pd.DataFrame(trades)
    if 'sell_date' not in df.columns and 'buy_date' not in df.columns:
        return {'level': 'unknown', 'message': '缺少日期信息'}

    # 按日期排序
    date_col = 'sell_date' if 'sell_date' in df.columns else 'buy_date'
    df = df.sort_values(date_col).reset_index(drop=True)

    split = int(len(df) * train_ratio)
    if split < 5 or len(df) - split < 3:
        return {'level': 'unknown', 'message': '分段后数据不足'}

    train = df.iloc[:split]
    test = df.iloc[split:]

    def calc_metrics(d):
        if len(d) == 0:
            return None
        wr = (d['profit_pct'] > 0).mean() * 100
        avg_ret = d['profit_pct'].mean()
        sharpe = avg_ret / d['profit_pct'].std() * np.sqrt(252) if d['profit_pct'].std() > 0 else 0
        return {'trades': len(d), 'win_rate': wr, 'avg_return': avg_ret, 'sharpe': sharpe}

    train_m = calc_metrics(train)
    test_m = calc_metrics(test)

    if not train_m or not test_m:
        return {'level': 'unknown', 'message': '指标计算失败'}

    # 判定
    wr_gap = abs(train_m['win_rate'] - test_m['win_rate'])
    sharpe_gap = abs(train_m['sharpe'] - test_m['sharpe'])

    if wr_gap > 20 or sharpe_gap > 1.0:
        level = 'high'
        msg = f"样本内胜率{train_m['win_rate']:.0f}% vs 样本外{test_m['win_rate']:.0f}% (差{wr_gap:.0f}ppt)，Sharpe差{sharpe_gap:.2f}，过拟合风险高"
    elif wr_gap > 10 or sharpe_gap > 0.5:
        level = 'medium'
        msg = f"样本内胜率{train_m['win_rate']:.0f}% vs 样本外{test_m['win_rate']:.0f}% (差{wr_gap:.0f}ppt)，Sharpe差{sharpe_gap:.2f}，存在一定过拟合"
    else:
        level = 'low'
        msg = f"样本内胜率{train_m['win_rate']:.0f}% vs 样本外{test_m['win_rate']:.0f}% (差{wr_gap:.0f}ppt)，过拟合风险低"

    return {
        'level': level,
        'message': msg,
        'train': train_m,
        'test': test_m,
        'wr_gap': round(wr_gap, 1),
        'sharpe_gap': round(sharpe_gap, 2),
    }


def monte_carlo_shuffle(trades, n_simulations=1000):
    """Monte Carlo模拟：打乱交易顺序，看实际结果在模拟分布的位置

    返回:
        dict: {actual_return, p50, p95_loss, p5_gain, rank_pct, is_outlier}
    """
    if not trades or len(trades) < 5:
        return None

    profits = np.array([t.get('profit_pct', 0) for t in trades])

    # 实际收益率（累乘）
    actual = np.prod(1 + profits / 100) - 1

    # Monte Carlo：打乱顺序1000次
    sim_returns = []
    for _ in range(n_simulations):
        np.random.shuffle(profits)
        sim_ret = np.prod(1 + profits / 100) - 1
        sim_returns.append(sim_ret)

    sim_returns = np.array(sim_returns)
    p50 = np.median(sim_returns)
    p95_loss = np.percentile(sim_returns, 5)    # 95%置信区间下限
    p5_gain = np.percentile(sim_returns, 95)     # 95%置信区间上限
    rank_pct = (sim_returns < actual).mean() * 100  # 实际收益超过多少%的模拟

    is_outlier = actual > p5_gain or actual < p95_loss

    return {
        'actual_return': round(actual * 100, 2),
        'p50': round(p50 * 100, 2),
        'p95_loss': round(p95_loss * 100, 2),
        'p5_gain': round(p5_gain * 100, 2),
        'rank_pct': round(rank_pct, 1),
        'is_outlier': is_outlier,
    }
