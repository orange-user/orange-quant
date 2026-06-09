#!/usr/bin/env python3
"""D1D2 全量回测报告生成"""
import sys, os, json, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime

RESULTS_FILE = os.path.join(os.path.dirname(__file__), 'backtest_d1d2_v3_results.json')
REPORT_FILE = os.path.join(os.path.dirname(__file__), 'backtest_report.txt')


def load():
    with open(RESULTS_FILE, encoding='utf-8') as f:
        return pd.DataFrame(json.load(f))


def write_report(df):
    lines = []
    def L(s=''): lines.append(s)
    def sep(): L('=' * 72)
    def sub(): L('-' * 72)
    def hr(): L('-' * 72)

    sep()
    L('  D1D2 全量回测报告')
    L(f'  生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    L(f'  数据源: SQLite缓存 2839只股票')
    L(f'  条件: D1涨>5% 量比>1.3 + D2从D1收盘回撤1~8%')
    sep()

    # ── 总体统计 ──
    hr()
    L(f'  总信号: {len(df)}笔')
    L(f'  覆盖股票: {df["code"].nunique()}只')
    L(f'  日期范围: {df["entry_date"].min()} ~ {df["entry_date"].max()}')
    L()

    for hold in range(1, 4):
        sub = df[df['hold_days'] == hold]
        if sub.empty: continue
        w = sub[sub['is_win']]
        wr = len(w) / len(sub) * 100
        avg = sub['pnl_pct'].mean()
        med = sub['pnl_pct'].median()
        sd = sub['pnl_pct'].std()
        sharpe = avg / sd * np.sqrt(244) if sd > 0 else 0
        L(f'  T+{hold}  ({len(sub):>5}笔)')
        L(f'    胜率: {wr:.1f}% ({len(w)}/{len(sub)})')
        L(f'    平均: {avg:+.2f}%  |  中位: {med:+.2f}%  |  标准差: {sd:.2f}%')
        L(f'    夏普: {sharpe:.2f}  |  最大盈: {sub["pnl_pct"].max():+.2f}%  |  最大亏: {sub["pnl_pct"].min():+.2f}%')
        L(f'    盈亏比: {abs(w["pnl_pct"].mean() / sub[~sub["is_win"]]["pnl_pct"].mean()):.2f}')
        L()

    # ── D1涨幅分桶 ──
    hr()
    L('  D1涨幅分桶 (T+1)')
    hr()
    t1 = df[df['hold_days'] == 1]
    L(f'  {"D1涨幅":>10}  {"笔数":>5}  {"胜率":>6}  {"平均":>8}  {"盈亏比":>6}')
    L(f'  {"-"*10}  {"-"*5}  {"-"*6}  {"-"*8}  {"-"*6}')
    for lo, hi, lbl in [(5, 6, '5-6%'), (6, 8, '6-8%'), (8, 10, '8-10%'), (10, 999, '>=10%')]:
        sub = t1[(t1['d1_chg'] >= lo) & (t1['d1_chg'] < hi)]
        if sub.empty: continue
        w = sub[sub['is_win']]
        r = abs(w['pnl_pct'].mean() / sub[~sub['is_win']]['pnl_pct'].mean()) if len(sub[~sub['is_win']]) > 0 else 0
        L(f'  {lbl:>10}  {len(sub):>5}  {len(w)/len(sub)*100:>5.1f}%  {sub["pnl_pct"].mean():>+7.2f}%  {r:>5.2f}')
    L()

    # ── D2回撤分桶 ──
    hr()
    L('  D2回撤分桶 (T+1)')
    hr()
    L(f'  {"回撤":>10}  {"笔数":>5}  {"胜率":>6}  {"平均":>8}  {"盈亏比":>6}')
    L(f'  {"-"*10}  {"-"*5}  {"-"*6}  {"-"*8}  {"-"*6}')
    for lo, hi, lbl in [(1, 2, '1-2%'), (2, 3, '2-3%'), (3, 4, '3-4%'), (4, 6, '4-6%'), (6, 999, '>=6%')]:
        sub = t1[(t1['d2_pullback'] >= lo) & (t1['d2_pullback'] < hi)]
        if sub.empty: continue
        w = sub[sub['is_win']]
        r = abs(w['pnl_pct'].mean() / sub[~sub['is_win']]['pnl_pct'].mean()) if len(sub[~sub['is_win']]) > 0 else 0
        L(f'  {lbl:>10}  {len(sub):>5}  {len(w)/len(sub)*100:>5.1f}%  {sub["pnl_pct"].mean():>+7.2f}%  {r:>5.2f}')
    L()

    # ── 月度收益 ──
    hr()
    L('  月度表现 (T+1)')
    hr()
    t1['month'] = t1['entry_date'].str[:7]
    monthly = t1.groupby('month').agg(
        笔数=('pnl_pct', 'count'),
        胜率=('is_win', 'mean'),
        平均收益=('pnl_pct', 'mean'),
        累计=('pnl_pct', 'sum'),
    )
    L(f'  {"月份":>7}  {"笔数":>5}  {"胜率":>6}  {"平均":>8}  {"月累计":>8}')
    L(f'  {"-"*7}  {"-"*5}  {"-"*6}  {"-"*8}  {"-"*8}')
    for idx, row in monthly.iterrows():
        L(f'  {idx:>7}  {row["笔数"]:>5}  {row["胜率"]*100:>5.1f}%  {row["平均收益"]:>+7.2f}%  {row["累计"]:>+7.2f}%')
    L(f'  {"合计":>7}  {len(t1):>5}  {t1["is_win"].mean()*100:>5.1f}%  {t1["pnl_pct"].mean():>+7.2f}%  {t1["pnl_pct"].sum():>+7.2f}%')
    L()

    # ── 最大回撤 / 连续亏损 ──
    hr()
    L('  风险分析 (T+1)')
    hr()
    t1_sorted = t1.sort_values('entry_date').reset_index(drop=True)
    equity = t1_sorted['pnl_pct'].cumsum()
    rolling_max = equity.cummax()
    dd = (equity - rolling_max)
    max_dd = dd.min()
    max_dd_idx = dd.idxmin()
    L(f'  最大回撤: {max_dd:.2f}%  (第{max_dd_idx+1}笔交易)')
    l = [f'  最大连亏: ']
    streaks = []
    cur = 0
    for _, r in t1_sorted.iterrows():
        if not r['is_win']:
            cur += 1
        else:
            if cur > 0: streaks.append(cur)
            cur = 0
    if cur > 0: streaks.append(cur)
    l.append(f'{max(streaks) if streaks else 0}笔'); L(''.join(l))
    L(f'  最大连盈: ', end='')
    streaks = []
    cur = 0
    for _, r in t1_sorted.iterrows():
        if r['is_win']:
            cur += 1
        else:
            if cur > 0: streaks.append(cur)
            cur = 0
    if cur > 0: streaks.append(cur)
    l.append(f'{max(streaks) if streaks else 0}笔'); L(''.join(l))
    L(f'  平均持仓: {t1_sorted["hold_days"].mean():.1f}天')
    L()

    # ── Top/Bottom 交易 ──
    hr()
    L('  最佳/最差 交易')
    hr()
    top5 = t1_sorted.nlargest(5, 'pnl_pct')
    L(f'  Top5:')
    for _, r in top5.iterrows():
        L(f'    {r["code"]}  D1{r["d1_chg"]:.0f}% 回撤{r["d2_pullback"]:.1f}%  {r["entry_date"]}  {r["pnl_pct"]:+.2f}%')
    worst5 = t1_sorted.nsmallest(5, 'pnl_pct')
    L(f'  Worst5:')
    for _, r in worst5.iterrows():
        L(f'    {r["code"]}  D1{r["d1_chg"]:.0f}% 回撤{r["d2_pullback"]:.1f}%  {r["entry_date"]}  {r["pnl_pct"]:+.2f}%')
    L()

    # ── 综合评分 ──
    sep()
    total_pnl = t1['pnl_pct'].sum()
    n_trades = len(t1)
    wr = t1['is_win'].mean() * 100
    avg_pnl = t1['pnl_pct'].mean()
    sharpe = avg_pnl / t1['pnl_pct'].std() * np.sqrt(244) if t1['pnl_pct'].std() > 0 else 0
    profit_factor = abs(t1[t1['is_win']]['pnl_pct'].sum() / t1[~t1['is_win']]['pnl_pct'].sum()) if t1[~t1['is_win']]['pnl_pct'].sum() != 0 else float('inf')

    L(f'  D1D2 策略综合评分')
    L()
    L(f'  总信号: {n_trades}笔')
    L(f'  胜率: {wr:.1f}%')
    L(f'  笔均收益: {avg_pnl:+.2f}%')
    L(f'  累计收益(T+1): {total_pnl:+.2f}%')
    L(f'  夏普比率: {sharpe:.2f}')
    L(f'  盈亏比: {profit_factor:.2f}')
    L(f'  最大回撤: {max_dd:.2f}%')
    L()
    L(f'  评分: ', end='')
    score = 0
    if wr > 60: score += 30
    elif wr > 55: score += 20
    else: score += 10
    if avg_pnl > 2: score += 30
    elif avg_pnl > 1: score += 20
    else: score += 10
    if sharpe > 2: score += 25
    elif sharpe > 1: score += 15
    else: score += 5
    if max_dd > -10: score += 15
    elif max_dd > -20: score += 10
    else: score += 5
    grade = 'S' if score >= 90 else 'A' if score >= 75 else 'B' if score >= 60 else 'C'
    L(f'{score}/100 (评级{grade})')
    sep()

    return '\n'.join(lines)


def main():
    print('生成D1D2全量回测报告...')
    df = load()
    report = write_report(df)
    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        f.write(report)
    print(report)
    print(f'\n报告已保存: {REPORT_FILE}')


if __name__ == '__main__':
    main()
