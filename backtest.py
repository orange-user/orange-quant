"""
Pulse Orange 回测脚本
对比改前改后评分效果: python backtest.py
"""
import sys, os, json, datetime, argparse
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
from data import get_stock_daily_cached, _get_pool_snapshot
from engine import calculate_comprehensive_score
from config import ACCOUNT_CAPITAL

def run_backtest(date_str=None, top_n=15):
    """跑一次完整扫描并输出评分结果"""
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 获取股票池...")
    pool = _get_pool_snapshot()
    if pool is None or len(pool) == 0:
        print("FAIL: 无法获取股票池")
        return None

    # 标准过滤
    pool = pool[~pool['名称'].str.contains('ST|退')]
    pool = pool[pool['总市值'] > 50e8]
    pool = pool[pool['最新价'] > 3]
    max_price = ACCOUNT_CAPITAL * 0.8 / 100
    pool = pool[pool['最新价'] <= max_price]
    pool = pool[pool['涨跌幅'].abs() < 9.5]

    codes = pool['代码'].tolist()
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 股票池: {len(codes)}只")

    # 快速预筛
    from engine import quick_prescreen
    from concurrent.futures import ThreadPoolExecutor as TPE
    with TPE(max_workers=48) as ex:
        prescores = list(ex.map(quick_prescreen, codes))

    top_codes = [p['code'] for p in sorted(prescores, key=lambda x: x['prescore'], reverse=True)[:40]]
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 预筛完成, 取前{len(top_codes)}只全量评分...")

    # 全量评分
    results = []
    with TPE(max_workers=48) as ex:
        futures = {ex.submit(calculate_comprehensive_score, c): c for c in top_codes}
        for fut in futures:
            try:
                res = fut.result(timeout=30)
                if res and res['signal'] > 30:
                    row = pool[pool['代码'] == res['code']]
                    if len(row) > 0:
                        r = row.iloc[0]
                        res['name'] = r['名称']
                        res['price'] = r['最新价']
                        res['change_pct'] = r['涨跌幅']
                        results.append(res)
            except:
                pass

    results.sort(key=lambda x: x['signal'], reverse=True)

    print(f"\n{'='*80}")
    print(f"{'TOP 15 评分结果':^80}")
    print(f"{'='*80}")
    print(f"{'排名':>4} {'代码':>8} {'名称':>10} {'信号':>6} {'涨幅':>8} {'RSI':>6} {'KDJ_J':>6} {'BB_pos':>8} {'ADX':>6} {'量比':>6} {'理由'}")
    print(f"{'-'*80}")

    for i, r in enumerate(results[:top_n]):
        reasons = r.get('priority_reason', '')[:40]
        print(f"{i+1:>4} {r['code']:>8} {r.get('name','?'):>10} {r['signal']:>6} {r.get('change_pct',0):>7.2f}% "
              f"{r.get('rsi',0):>6.1f} {r.get('kdj_j',0):>6.1f} {r.get('bb_position',0):>8.2f} "
              f"{r.get('adx',0):>6.1f} {r.get('volume_ratio',0):>6.2f} {reasons}")

    print(f"{'='*80}")

    # 风险指标统计
    overbought = sum(1 for r in results if r.get('kdj_j', 0) > 80)
    high_rsi = sum(1 for r in results if r.get('rsi', 0) > 65)
    high_bb = sum(1 for r in results if r.get('bb_position', 0) > 0.85)
    print(f"\n风险统计 (前{len(results)}只):")
    print(f"  KDJ超买(J>80): {overbought}只 ({overbought/len(results)*100:.0f}%)" if results else "")
    print(f"  RSI偏高(>65):   {high_rsi}只 ({high_rsi/len(results)*100:.0f}%)" if results else "")
    print(f"  布林上轨(>0.85):{high_bb}只 ({high_bb/len(results)*100:.0f}%)" if results else "")

    return results


def compare_versions():
    """对比两个版本的评分结果 (需手动保存快照)"""
    baseline_file = "backtest_baseline.json"
    current_file = "backtest_current.json"

    if os.path.exists(baseline_file):
        print("发现基线文件, 运行扫描与基线对比...")
    else:
        print("首次运行, 生成基线文件...")

    results = run_backtest()
    if results is None:
        return

    # 保存结果
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    with open(f"backtest_{timestamp}.json", 'w') as f:
        json.dump(results[:15], f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到 backtest_{timestamp}.json")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Pulse Orange 回测工具')
    parser.add_argument('--action', choices=['run', 'compare'], default='run', help='run=跑扫描, compare=对比')
    args = parser.parse_args()

    if args.action == 'compare':
        compare_versions()
    else:
        run_backtest()
