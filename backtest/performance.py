"""回测绩效报告"""
import logging
import pandas as pd
import numpy as np

from backtest.overfit import walk_forward_check, monte_carlo_shuffle

logger = logging.getLogger('backtest.performance')


def generate_report(backtest_result):
    """生成完整绩效报告"""
    if backtest_result is None:
        return {"error": "回测未运行"}

    trades = backtest_result.get('trades', [])
    daily = backtest_result.get('daily_snapshots', [])
    initial_capital = backtest_result.get('initial_capital', 100000)
    final_capital = backtest_result.get('final_capital', initial_capital)

    if not trades:
        report_text = """
+-------------------------------------------------------+
|           Pulse Orange Backtest Report                 |
|           No trades generated                          |
+-------------------------------------------------------+
"""
        print(report_text)
        return {"error": "无交易记录", "total_trades": 0}

    # ===== 基础指标 =====
    total_profit = final_capital - initial_capital
    total_return_pct = (final_capital / initial_capital - 1) * 100

    n_days = len(daily)
    if n_days > 0:
        annual_return = (final_capital / initial_capital) ** (252 / n_days) - 1
    else:
        annual_return = 0

    # ===== 胜率 =====
    df_t = pd.DataFrame(trades)
    wins = df_t[df_t['profit_pct'] > 0]
    losses = df_t[df_t['profit_pct'] <= 0]
    win_rate = len(wins) / len(df_t) * 100

    # ===== 盈亏比 =====
    avg_win = wins['profit_pct'].mean() if len(wins) > 0 else 0
    avg_loss = abs(losses['profit_pct'].mean()) if len(losses) > 0 else 0
    profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')

    # 总盈亏金额
    total_profit_val = df_t['profit_val'].sum()

    # ===== 最大回撤 =====
    df_d = pd.DataFrame(daily)
    df_d['peak'] = df_d['total_value'].cummax()
    df_d['drawdown'] = (df_d['total_value'] - df_d['peak']) / df_d['peak'] * 100
    max_drawdown = df_d['drawdown'].min()

    # ===== 夏普比率 =====
    df_d['daily_return'] = df_d['total_value'].pct_change()
    rf_daily = 0.02 / 252
    excess = df_d['daily_return'].dropna() - rf_daily
    sharpe = (np.sqrt(252) * excess.mean() / excess.std()
              if excess.std() > 0 else 0)

    # ===== 持仓天数 =====
    avg_hold = df_t['hold_days'].mean() if 'hold_days' in df_t.columns else 0

    # ===== 最大连续亏损 =====
    df_t['win'] = df_t['profit_pct'] > 0
    max_consec_loss = 0
    curr = 0
    for w in df_t['win']:
        if not w:
            curr += 1
            max_consec_loss = max(max_consec_loss, curr)
        else:
            curr = 0

    # ===== 按卖出原因分类统计 =====
    reason_stats = {}
    for t in trades:
        reason = t.get('sell_reason', '未知')
        if reason not in reason_stats:
            reason_stats[reason] = {'count': 0, 'profit_vals': [], 'profit_pcts': []}
        reason_stats[reason]['count'] += 1
        reason_stats[reason]['profit_vals'].append(t['profit_val'])
        reason_stats[reason]['profit_pcts'].append(t['profit_pct'])

    # ===== 输出报告 =====
    report = f"""
+-------------------------------------------------------+
|           Pulse Orange Backtest Report                 |
+-------------------------------------------------------+
| Period:  {backtest_result['config']['start_date']} ~ {backtest_result['config']['end_date']}   |
| Trading Days:  {n_days}                                    |
| Daily Buy:  TOP{backtest_result['config']['top_n']}                                    |
+-------------------------------------------------------+
| Initial Capital:  {initial_capital:>10,.0f}                            |
| Final Capital:    {final_capital:>10,.0f}                            |
| Total P&L:        {total_profit_val:>+10,.0f}  ({total_return_pct:+.2f}%)           |
| Annual Return:    {annual_return * 100:+.2f}%                            |
+-------------------------------------------------------+
| Total Trades: {len(trades):>5d}                                    |
| Win Rate:      {win_rate:.1f}%                                     |
| Avg Win:  {avg_win:+.2f}%   Avg Loss: {avg_loss:.2f}%                      |
| Profit Factor: {profit_factor:.2f}                                    |
| Max Consec Loss: {max_consec_loss}                                   |
+-------------------------------------------------------+
| Sharpe Ratio:  {sharpe:.2f}                                     |
| Max Drawdown:  {max_drawdown:.2f}%                                    |
| Avg Hold Days: {avg_hold:.1f}                                    |
+-------------------------------------------------------+
"""

    # 按原因统计
    reason_lines = []
    for reason, stats in sorted(reason_stats.items(),
                                 key=lambda x: sum(x[1]['profit_vals']),
                                 reverse=True):
        avg_p = np.mean(stats['profit_pcts']) if stats['profit_pcts'] else 0
        total_p = sum(stats['profit_vals'])
        reason_lines.append(
            f"  {reason:<20} {stats['count']:>3}笔  均盈亏{avg_p:>+7.2f}%  "
            f"总额{total_p:>+8.0f}"
        )

    print(report)

    if reason_lines:
        print("--- 卖出原因分析 ---")
        for line in reason_lines:
            print(line)

    # ===== 过拟合分析 =====
    wf = walk_forward_check(trades)
    mc = monte_carlo_shuffle(trades)

    print("\n--- 过拟合分析 ---")
    if wf and wf['level'] != 'unknown':
        print(f"  Walk-forward: {wf['message']}")
        if wf.get('train'):
            print(f"    训练集: {wf['train']['trades']}笔 胜率{wf['train']['win_rate']:.0f}% "
                  f"Sharpe{wf['train']['sharpe']:.2f}")
        if wf.get('test'):
            print(f"    测试集: {wf['test']['trades']}笔 胜率{wf['test']['win_rate']:.0f}% "
                  f"Sharpe{wf['test']['sharpe']:.2f}")
    else:
        print(f"  Walk-forward: 数据不足，跳过")

    if mc:
        outlier_label = "[异常]" if mc['is_outlier'] else "[正常]"
        print(f"  Monte Carlo ({1000}次): 实际{mc['actual_return']:.1f}% | "
              f"中位数{mc['p50']:.1f}% | "
              f"95%CI [{mc['p95_loss']:.1f}%, {mc['p5_gain']:.1f}%] | "
              f"排名P{mc['rank_pct']:.0f} | {outlier_label}")
    else:
        print(f"  Monte Carlo: 数据不足，跳过")

    # 交易明细（显示前50笔）
    print(f"\n--- 交易明细 ({len(trades)}笔) ---")
    print(f"{'#':>4} {'代码':>8} {'买入':>8} {'卖出':>8} {'盈亏%':>8} "
          f"{'盈亏额':>8} {'持仓':>4} {'信号':>5} {'卖出原因'}")
    print("-" * 75)
    display_trades = trades[:50]
    for i, t in enumerate(display_trades):
        print(f"{i+1:>4} {t['code']:>8} {t['buy_price']:>8.2f} "
              f"{t['sell_price']:>8.2f} {t['profit_pct']:>+7.2f}% "
              f"{t['profit_val']:>+8.0f} {t['hold_days']:>4d}天 "
              f"{t['signal']:>5d} {t.get('sell_reason',''):20.20s}")
    if len(trades) > 50:
        print(f"  ... 还有 {len(trades)-50} 笔")

    # 资金曲线
    if len(daily) > 0:
        print(f"\n--- 资金曲线 (每5天) ---")
        print(f"{'日期':<12} {'总资产':>10} {'现金':>10} {'持仓':>6}")
        for i, d in enumerate(daily):
            if i % 5 == 0 or i == len(daily) - 1:
                print(f"{d['date']:<12} {d['total_value']:>10,.0f} "
                      f"{d['cash']:>10,.0f} {d['position_count']:>6}")

    return {
        'total_return_pct': round(total_return_pct, 2),
        'annual_return': round(annual_return * 100, 2),
        'sharpe': round(sharpe, 2),
        'max_drawdown': round(max_drawdown, 2),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2),
        'total_trades': len(trades),
        'winning_trades': int(len(wins)),
        'losing_trades': int(len(losses)),
        'avg_hold_days': round(avg_hold, 1),
        'total_profit': round(total_profit_val, 2),
        'final_capital': round(final_capital, 2),
    }
