"""Pulse Orange 历史回测 CLI入口

用法:
    python -m backtest.main                    # 默认3个月回测
    python -m backtest.main --warm             # 仅预热数据
    python -m backtest.main --start 2026-03-01 --end 2026-05-31
    python -m backtest.main --capital 100000 --top 3
"""
import sys
import os
import argparse
import json
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.data_provider import warmup_cache
from backtest.runner import run_backtest
from backtest.performance import generate_report

logger = logging.getLogger('backtest')


def main():
    parser = argparse.ArgumentParser(
        description='Pulse Orange 历史回测',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m backtest.main --warm              # 预热数据
  python -m backtest.main                      # 默认回测(近3个月)
  python -m backtest.main --top 5             # 每日买TOP5
  python -m backtest.main --start 2026-03-01 --end 2026-05-31
        """)
    parser.add_argument('--start', type=str, default=None,
                        help='开始日期 YYYY-MM-DD (默认: 3个月前)')
    parser.add_argument('--end', type=str, default=None,
                        help='结束日期 YYYY-MM-DD (默认: 今天)')
    parser.add_argument('--capital', type=float, default=100000,
                        help='初始资金 (默认: 100000)')
    parser.add_argument('--top', type=int, default=3,
                        help='每日买入数量 (默认: 3)')
    parser.add_argument('--threshold', type=int, default=85,
                        help='强势信号阈值触发回落条件单 (默认: 85)')
    parser.add_argument('--slippage', type=float, default=0.001,
                        help='买卖滑点比例 (默认: 0.001 = 10bps, 0=无滑点)')
    parser.add_argument('--warm', action='store_true',
                        help='仅预热数据缓存，不运行回测')
    parser.add_argument('--output', type=str, default='backtest_result.json',
                        help='结果输出文件 (默认: backtest_result.json)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='详细日志')

    args = parser.parse_args()

    # 日志级别
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[logging.StreamHandler()]
    )

    # 日期处理
    end = args.end if args.end else datetime.now().strftime('%Y-%m-%d')
    start = args.start if args.start else (
            datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    print("=" * 60)
    print("  Pulse Orange Li Shi Hui Ce")
    print("=" * 60)
    print(f"  Qi Jian: {start} ~ {end}")
    print(f"  Chu Shi Zi Jin: {args.capital:.0f}  Mei Ri Mai Ru: TOP{args.top}")
    print(f"  Qiang Shi Xin Hao Yu Zhi: {args.threshold}")
    print("=" * 60)
    print()

    # 预热模式
    if args.warm:
        print("[Warm] Pre-heating data cache...")
        result = warmup_cache(target_days=90)
        print(f"  Done: cached={result['already_cached']}, "
              f"new={result['warmed']}, failed={result['failed']}")
        return

    # 运行回测
    print("[Run] Starting backtest...\n")
    result = run_backtest(
        start_date=start,
        end_date=end,
        initial_capital=args.capital,
        top_n=args.top,
        strong_signal_threshold=args.threshold,
        slippage=args.slippage,
    )

    if result is None:
        print("[Error] Backtest failed: insufficient data")
        sys.exit(1)

    # 生成报告
    print("\n" + "=" * 60)
    metrics = generate_report(result)

    # 保存结果（含交易明细）
    trades = result.get('trades', [])
    # 精简交易明细：只保留分析需要的字段
    trade_summary = []
    for t in trades:
        trade_summary.append({
            'code': t['code'],
            'buy_date': t['buy_date'],
            'sell_date': t.get('sell_date', ''),
            'buy_price': t['buy_price'],
            'sell_price': t['sell_price'],
            'profit_pct': t['profit_pct'],
            'profit_val': t['profit_val'],
            'signal': t['signal'],
            'sell_reason': t.get('sell_reason', ''),
            'hold_days': t.get('hold_days', 1),
        })
    output = {
        'config': {
            'start': start,
            'end': end,
            'capital': args.capital,
            'top_n': args.top,
            'threshold': args.threshold,
            'slippage': args.slippage,
        },
        'trades': trade_summary,
        'metrics': {k: v for k, v in metrics.items()
                    if k not in ('error',)},
        'trade_count': len(trades),
        'final_capital': result.get('final_capital', 0),
    }
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {args.output}")


if __name__ == '__main__':
    main()
