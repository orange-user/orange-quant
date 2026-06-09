"""Pulse Orange v4 统一策略启动器 (优化版)
配置驱动, 支持单策略/多策略/组合回测
优化: 信号预计算 → 回测循环解耦, 大幅提升速度
"""
import sys, os, json, time, logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR

from v4_base import (
    Strategy, read_sqlite_daily, get_eligible_codes,
    compute_metrics, print_metrics, SLIPPAGE, trade_cost,
)
from v4_strategies import ALL_STRATEGIES
from v4_perception import (
    MarketPerception, EmotionCycleClassifier, REGIME_CN,
    get_enabled_strategies, is_strategy_allowed,
)

logger = logging.getLogger('v4.runner')


# ====================================================================
# 信号预计算: 对每只股票提前算出每天是否触发买入
# ====================================================================
def precompute_signals(strategy, codes, output_file=None):
    """预计算策略在所有股票上的买入信号

    Returns:
        signal_map: {date_str: [(code, entry_df_idx), ...]}
    """
    logger.info(f"预计算信号: {strategy.name} ({len(codes)}只股票)")

    signal_map = {}
    code_data = {}
    all_dates = set()

    for code in codes:
        df = read_sqlite_daily(code, min_days=60)
        if df is None:
            continue
        code_data[code] = df
        all_dates.update(df['date'].tolist())

        # 计算每只股票的entry信号
        for ci in range(60, len(df) - 1):
            if strategy.entry_signals(df, ci):
                date_str = str(df.iloc[ci]['date'])[:10]
                if date_str not in signal_map:
                    signal_map[date_str] = []
                signal_map[date_str].append((code, ci))

    logger.info(f"信号预计算完成: {len(signal_map)}天, {sum(len(v) for v in signal_map.values())}个信号")

    if output_file:
        # 只保存统计信息
        stats = {
            'strategy': strategy.name,
            'total_signals': sum(len(v) for v in signal_map.values()),
            'signal_days': len(signal_map),
        }
        with open(output_file, 'w') as f:
            json.dump(stats, f, indent=2)

    return signal_map, code_data, sorted(all_dates)


# ====================================================================
# 回测执行 (基于预计算信号)
# ====================================================================
def run_backtest_from_signals(
    strategy, signal_map, code_data, all_dates,
    capital=100000, slippage=SLIPPAGE, max_positions=2,
    start_pct=0.0, end_pct=1.0,
    idx_df=None, use_perception=True,
):
    """从预计算信号运行回测

    解耦了信号计算和资金模拟, 大幅提升多策略回测效率
    """
    start_idx = int(len(all_dates) * start_pct) if start_pct > 0 else 0
    end_idx = int(len(all_dates) * end_pct) if end_pct < 1.0 else len(all_dates) - 1

    trading_dates = all_dates[start_idx:end_idx]
    if len(trading_dates) < 20:
        return {'error': '数据不足', 'strategy': strategy.name}

    logger.info(f"回测: {trading_dates[0]} ~ {trading_dates[-1]} ({len(trading_dates)}天)")

    # 感知层
    perception = MarketPerception()

    # 回测状态
    cash = capital
    positions = []
    trades = []
    daily_values = []
    date_set = set(trading_dates)

    for di, today in enumerate(trading_dates):
        today_str = str(today)[:10]

        # ── 感知层 ──
        perception_result = None
        if use_perception and idx_df is not None:
            idx_match = idx_df[idx_df['date'] == today]
            if len(idx_match) > 0:
                idx_pos = idx_match.index[0]
                perception_result = perception.perceive(
                    idx_df=idx_df, date_idx=idx_pos)
                if not perception_result['trade_allowed']:
                    # 记录但不交易
                    pos_value = sum(
                        p['shares'] * code_data.get(p['code'], pd.DataFrame()).iloc[-1]['close']
                        if code_data.get(p['code']) is not None and len(code_data[p['code']]) > 0
                        else p['buy_price']
                        for p in positions
                    ) if positions else 0
                    total = cash + pos_value
                    daily_values.append({
                        'date': today_str, 'cash': round(cash, 2),
                        'position_value': round(pos_value, 2),
                        'total_value': round(total, 2),
                        'positions': len(positions),
                        'regime': perception_result['regime'],
                        'block_reason': perception_result['block_reason'],
                    })
                    continue

        # ── 卖出 ──
        for pos in list(positions):
            code = pos['code']
            df = code_data.get(code)
            if df is None:
                continue

            ci = df[df['date'] == today].index
            if len(ci) == 0:
                continue
            ci = ci[0]
            entry_dfi = pos['entry_df_idx']
            days_held = di - pos['buy_day_idx']

            should_sell = False
            reason = ''

            # 策略自身卖出
            should_sell_s, reason_s = strategy.exit_signals(df, entry_dfi, ci)
            if should_sell_s:
                should_sell = True
                reason = reason_s

            # S5衰竭卖出(所有策略共用)
            from v4_strategies import S5_PushExhaustion
            s5 = S5_PushExhaustion()
            should_exhaust, reason_ex = s5.exit_signals(df, entry_dfi, ci)
            if should_exhaust:
                should_sell = True
                reason = reason_ex

            # 强制时间卖出
            if days_held >= strategy.max_hold_days:
                should_sell = True
                reason = 'max_hold'

            if should_sell:
                row = df.iloc[ci]
                sell_price = float(row['close']) * (1 - slippage)
                proceeds = trade_cost(sell_price, pos['shares'], False)
                pnl_pct = (proceeds - pos['cost_basis']) / pos['cost_basis'] * 100
                trades.append({
                    'code': code, 'strategy': strategy.name,
                    'buy_date': pos['buy_date'], 'sell_date': today_str,
                    'buy_price': pos['buy_price'],
                    'sell_price': round(sell_price, 2),
                    'shares': pos['shares'],
                    'pnl_pct': round(pnl_pct, 2),
                    'pnl_val': round(proceeds - pos['cost_basis'], 2),
                    'hold_days': days_held,
                    'reason': reason,
                })
                cash += proceeds
                positions.remove(pos)

        # ── 持仓市值 ──
        pos_value = 0
        for pos in positions:
            df = code_data.get(pos['code'])
            if df is not None:
                row = df[df['date'] == today]
                if len(row) > 0:
                    cur_price = float(row.iloc[0]['close'])
                    pos['current_value'] = pos['shares'] * cur_price
                    pos_value += pos['current_value']
        total = cash + pos_value

        daily_values.append({
            'date': today_str, 'cash': round(cash, 2),
            'position_value': round(pos_value, 2),
            'total_value': round(total, 2),
            'positions': len(positions),
            'regime': perception_result['regime'] if perception_result else 'unknown',
        })

        # ── 买入 ──
        if len(positions) >= max_positions:
            continue

        # 从信号中获取今天的候选
        candidates = signal_map.get(today_str, [])
        if not candidates:
            continue

        # 剔除已持仓
        held_codes = {p['code'] for p in positions}
        candidates = [c for c in candidates if c[0] not in held_codes]
        if not candidates:
            continue

        # 买入第一个候选 (次日开盘)
        code, ci = candidates[0]
        df = code_data.get(code)
        if df is None or ci + 1 >= len(df):
            continue

        next_row = df.iloc[ci + 1]
        buy_price = float(next_row['open']) * (1 + slippage)
        shares_hands = max(1, int(cash * 0.5 / max_positions / (buy_price * 100)))
        shares = shares_hands * 100
        cost = trade_cost(buy_price, shares, True)

        if cost > cash:
            shares_hands = max(1, int(cash * 0.8 / (buy_price * 100)))
            shares = shares_hands * 100
            cost = trade_cost(buy_price, shares, True)
            if cost > cash:
                continue

        positions.append({
            'code': code, 'shares': shares, 'buy_price': buy_price,
            'cost_basis': cost,
            'buy_date': str(next_row['date'])[:10],
            'buy_day_idx': di, 'entry_df_idx': ci + 1,
            'current_value': shares * buy_price,
        })
        cash -= cost

    # ── 强制平仓 ──
    for pos in list(positions):
        df = code_data.get(pos['code'])
        if df is not None and len(df) > 0:
            last_row = df.iloc[-1]
            sell_price = float(last_row['close']) * (1 - slippage)
            proceeds = trade_cost(sell_price, pos['shares'], False)
            pnl_pct = (proceeds - pos['cost_basis']) / pos['cost_basis'] * 100
            trades.append({
                'code': pos['code'], 'strategy': strategy.name,
                'buy_date': pos['buy_date'],
                'sell_date': str(last_row['date'])[:10],
                'buy_price': pos['buy_price'],
                'sell_price': round(sell_price, 2),
                'shares': pos['shares'], 'pnl_pct': round(pnl_pct, 2),
                'pnl_val': round(proceeds - pos['cost_basis'], 2),
                'hold_days': len(trading_dates) - pos['buy_day_idx'],
                'reason': 'end_of_test',
            })
            cash += proceeds
        positions.remove(pos)

    return {
        'strategy': strategy.name,
        'description': strategy.description,
        'trades': trades,
        'daily': daily_values,
        'initial_capital': capital,
        'final_capital': round(cash, 2),
        'total_trades': len(trades),
        'period': f"{trading_dates[0]} ~ {trading_dates[-1]}",
    }


# ====================================================================
# 主入口: 信号预计算 → 批量回测
# ====================================================================
def run_all_with_signals(
    strategy_names=None,
    codes=None,
    capital=100000,
    slippage=SLIPPAGE,
    max_positions=2,
    start_pct=0.6,
    end_pct=0.95,
    use_perception=True,
    max_workers=4,
):
    """信号预计算 → 批量回测"""
    if strategy_names is None:
        strategy_names = list(ALL_STRATEGIES.keys())

    if codes is None:
        codes = get_eligible_codes()

    # 加载沪深300（感知层用）
    idx_df = None
    try:
        import akshare as _ak
        _idx = _ak.stock_zh_index_daily(symbol='sh000300')
        if _idx is not None and len(_idx) > 60:
            _idx = _idx.tail(400).copy()
            _idx['date'] = _idx['date'].astype(str).str.replace('-', '')
            for c in ['open','high','low','close','volume']:
                _idx[c] = pd.to_numeric(_idx[c], errors='coerce')
            idx_df = _idx.dropna(subset=['open','close']).reset_index(drop=True)
            logger.info(f"沪深300加载成功: {len(idx_df)}天")
    except Exception as e:
        logger.warning(f"沪深300加载失败: {e}")

    # 批量预计算信号
    all_signals = {}
    all_code_data = {}
    all_dates_set = set()

    for sname in strategy_names:
        if sname not in ALL_STRATEGIES:
            continue
        strategy = ALL_STRATEGIES[sname]()

        sm, cd, ad = precompute_signals(strategy, codes)
        all_signals[sname] = sm
        if not all_code_data:
            all_code_data = cd
        if ad:
            all_dates_set.update(ad)

    all_dates = sorted(all_dates_set)
    if len(all_dates) < 60:
        return {'error': f'数据不足{len(all_dates)}天'}

    # 批量运行回测
    results = {}
    for sname in strategy_names:
        if sname not in all_signals:
            continue
        strategy = ALL_STRATEGIES[sname]()
        logger.info(f"回测策略: {sname}")

        result = run_backtest_from_signals(
            strategy, all_signals[sname], all_code_data,
            all_dates, capital, slippage, max_positions,
            start_pct, end_pct, idx_df, use_perception,
        )
        if result and not result.get('error'):
            metrics = compute_metrics(result)
            results[sname] = {'result': result, 'metrics': metrics}
        else:
            logger.warning(f"{sname}: {result.get('error', '无结果')}")
            results[sname] = {'result': result, 'metrics': {'error': result.get('error', '未知')}}

    return results


def run_single_strategy(
    strategy_name, codes=None, capital=100000,
    slippage=SLIPPAGE, max_positions=2,
    start_pct=0.6, end_pct=0.95, use_perception=True,
):
    """运行单个策略"""
    results = run_all_with_signals(
        strategy_names=[strategy_name],
        codes=codes, capital=capital, slippage=slippage,
        max_positions=max_positions,
        start_pct=start_pct, end_pct=end_pct,
        use_perception=use_perception,
    )
    if strategy_name in results:
        return results[strategy_name]['result']
    return None


def run_all_strategies(codes=None, capital=100000, slippage=SLIPPAGE,
                       max_positions=2, start_pct=0.6, end_pct=0.95,
                       use_perception=True):
    """运行所有策略"""
    return run_all_with_signals(
        strategy_names=list(ALL_STRATEGIES.keys()),
        codes=codes, capital=capital, slippage=slippage,
        max_positions=max_positions,
        start_pct=start_pct, end_pct=end_pct,
        use_perception=use_perception,
    )


def print_all_results(results):
    """打印所有策略结果"""
    print("\n" + "=" * 70)
    print("  Pulse Orange v4 策略回测报告")
    print("=" * 70)

    rows = []
    for sname in sorted(results.keys()):
        m = results[sname].get('metrics', {})
        if 'error' in m:
            continue
        n = m.get('total_trades', 0)
        wr = m.get('win_rate', 0)
        sh = m.get('sharpe', 0)
        pnl = m.get('total_return_val', 0)
        mdd = m.get('max_drawdown', 0)
        pf = m.get('profit_factor', 0)
        ah = m.get('avg_hold_days', 0)
        ok = 'PASS' if (sh > 0.3 and n >= 10) else 'FAIL'
        rows.append({
            'Strategy': sname[:20],
            'Trades': n, 'Win%': f"{wr:.1f}",
            'Sharpe': f"{sh:.2f}", 'PnL': f"{pnl:+.0f}",
            'MDD%': f"{mdd:.1f}", 'PF': f"{pf:.2f}",
            'Hold': f"{ah:.1f}d", 'Status': ok,
        })

    if not rows:
        print("  无结果")
        return

    headers = list(rows[0].keys())
    col_w = {h: max(len(h), max(len(str(r[h])) for r in rows)) + 1 for h in headers}
    print('  ' + ''.join(h.center(col_w[h]) for h in headers))
    print('  ' + '-' * sum(col_w.values()))
    for r in rows:
        print('  ' + ''.join(str(r[h]).center(col_w[h]) for h in headers))

    print("=" * 70)


# ── 命令行入口 ──
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Pulse Orange v4 策略回测')
    parser.add_argument('--strategy', '-s', default='all', help='策略名或all或perception')
    parser.add_argument('--capital', '-c', type=float, default=100000, help='初始资金')
    parser.add_argument('--start', type=float, default=0.6, help='起始位置(0-1)')
    parser.add_argument('--end', type=float, default=0.95, help='结束位置(0-1)')
    parser.add_argument('--no-perception', action='store_true', help='禁用感知层')
    parser.add_argument('--save', action='store_true', help='保存结果')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')

    t0 = time.time()
    if args.strategy == 'all':
        results = run_all_strategies(
            capital=args.capital, start_pct=args.start, end_pct=args.end,
            use_perception=not args.no_perception,
        )
        print_all_results(results)
        if args.save:
            save = {s: {'metrics': d['metrics']} for s, d in results.items()}
            with open(os.path.join(DATA_DIR, 'v4_results.json'), 'w') as f:
                json.dump(save, f, indent=2)
            print(f"  已保存到 data/v4_results.json")

        # Portfolio
        from v4_portfolio import build_portfolio, portfolio_backtest, print_portfolio_report
        metrics_dict = {s: d['metrics'] for s, d in results.items()}
        daily_dict = {}
        for s, d in results.items():
            daily = d.get('result', {}).get('daily', [])
            if daily:
                daily_dict[s] = pd.DataFrame(daily)

        for method in ['auto', 'equal', 'risk_parity', 'kelly']:
            weights = build_portfolio(metrics_dict, daily_dict, method=method)
            if weights:
                port_result = portfolio_backtest(weights, results)
                print_portfolio_report(port_result, weights)

    elif args.strategy == 'perception':
        p = MarketPerception()
        r = p.emotion.classify_live()
        print(f"\n当前市场: {r['regime_cn']} (置信度{r['confidence']:.0%})")
        print(f"  投票: S1涨停结构={r['votes']['s1_zhangting']} S2量价={r['votes']['s2_liangjia']} S3宽度={r['votes']['s3_kuandu']}")
        print(f"  {r['raw']}")
    else:
        result = run_single_strategy(
            args.strategy, capital=args.capital,
            start_pct=args.start, end_pct=args.end,
            use_perception=not args.no_perception,
        )
        if result:
            print_metrics(compute_metrics(result))

    logger.info(f"总耗时: {time.time() - t0:.0f}s")
