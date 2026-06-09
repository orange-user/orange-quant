"""
回测卖出点分析：对比开盘卖 vs 回落条件单卖
取100只票的近期日线数据，模拟两种卖出策略，看实际差异
"""
import sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pandas as pd
import numpy as np
from config import DB_PATH
from collections import namedtuple

SellResult = namedtuple('SellResult', ['should_sell', 'sell_price', 'reason'])

# ========== 回落条件单参数（可调） ==========
TRAILING_ACTIVATE = 1.5      # 涨幅≥1.5%启动回落
TRAILING_DRAWDOWN = 0.5      # 从峰值回落0.5%触发卖出
STOP_LOSS = -3.0              # 止损-3%
PROFIT_TAKE = 5.0             # 止盈+5%

def simulate_trailing(entry, open_p, high, low, close, prev_close,
                      activate_pct=None, drawdown_pct=None, stop_loss=None):
    """模拟回落条件单卖出，测试两种可能的日内路径"""
    activate_pct = activate_pct or TRAILING_ACTIVATE
    drawdown_pct = drawdown_pct or TRAILING_DRAWDOWN
    stop_loss = stop_loss or STOP_LOSS
    results = []

    for label, points in [
        ('阳线路径(开→低→高→收)', [
            ('open', open_p), ('low', low), ('high', high), ('close', close)
        ]),
        ('阴线路径(开→高→低→收)', [
            ('open', open_p), ('high', high), ('low', low), ('close', close)
        ]),
    ]:
        peak = entry
        trailing_active = False
        sold_at = None
        reason = '未触发'

        for pname, pprice in points:
            pct = (pprice - entry) / entry * 100

            # 止损
            if pct <= stop_loss:
                sold_at = pprice; reason = f'止损({pct:.1f}%)'; break

            # 激活回落条件
            if not trailing_active and pct >= activate_pct:
                trailing_active = True
                peak = pprice

            if trailing_active:
                if pprice > peak:
                    peak = pprice
                drawdown = (peak - pprice) / peak * 100
                if drawdown >= drawdown_pct:
                    sold_at = peak * (1 - drawdown_pct/100)
                    reason = f'回落触发(+{(peak-entry)/entry*100:.1f}%→回{drawdown:.1f}%)'
                    break

        if sold_at is None:
            # 日终未触发，看收盘
            close_pct = (close - entry) / entry * 100
            if close_pct <= stop_loss:
                sold_at = close; reason = f'日终止损({close_pct:.1f}%)'
            elif close_pct <= -2:
                sold_at = close; reason = f'日终趋弱({close_pct:.1f}%)'
            else:
                sold_at = close; reason = f'日终未触发(收{close_pct:.1f}%)'

        ret = (sold_at - entry) / entry * 100
        results.append({'path': label, 'sell_pct': round(ret, 2), 'reason': reason})

    return results


def main():
    conn = sqlite3.connect(DB_PATH)

    # 1. 取今天股票池（所有股票去重）
    today = pd.read_sql_query(
        "SELECT code, MAX(date) as last_date FROM daily_data GROUP BY code ORDER BY RANDOM() LIMIT 200",
        conn
    )
    conn.close()

    print(f"候选股票: {len(today)}只")
    print("=" * 90)

    # 2. 对每只股票取近30天数据
    all_trades = []
    stock_count = 0

    conn2 = sqlite3.connect(DB_PATH)
    for _, row in today.iterrows():
        code = row['code']
        df = pd.read_sql_query(
            "SELECT date, open, close, high, low, volume FROM daily_data "
            "WHERE code=? ORDER BY date DESC LIMIT 35",
            conn2, params=(code,)
        )
        if len(df) < 5:
            continue

        df = df.sort_values('date').reset_index(drop=True)

        # 逐天模拟：前一天收盘价买入，今日开盘卖出/回落卖
        for i in range(1, len(df)):
            prev = df.iloc[i-1]
            cur = df.iloc[i]

            entry = float(prev['close'])
            open_p = float(cur['open'])
            high = float(cur['high'])
            low = float(cur['low'])
            close = float(cur['close'])

            # 开盘价卖出
            open_ret = (open_p - entry) / entry * 100

            # 回落条件单卖出（取两种路径的均值）
            trail_results = simulate_trailing(entry, open_p, high, low, close, prev_close=None)
            trail_rets = [r['sell_pct'] for r in trail_results]
            avg_trail_ret = sum(trail_rets) / len(trail_rets)

            all_trades.append({
                'code': code,
                'date': cur['date'],
                'entry': round(entry, 2),
                'open': round(open_p, 2),
                'high': round(high, 2),
                'low': round(low, 2),
                'close': round(close, 2),
                'open_ret': round(open_ret, 2),
                'avg_trail_ret': round(avg_trail_ret, 2),
                'trail_优势': round(avg_trail_ret - open_ret, 2),
                '阳线路径': trail_results[0]['sell_pct'],
                '阴线路径': trail_results[1]['sell_pct'],
                '阳线原因': trail_results[0]['reason'],
                '阴线原因': trail_results[1]['reason'],
            })

        stock_count += 1
        if stock_count >= 100:
            break

    conn2.close()

    if not all_trades:
        print("没有足够数据！")
        return

    df_r = pd.DataFrame(all_trades)
    print(f"总样本数: {len(df_r)} 笔 (来自 {stock_count} 只股票)")
    print()

    # ===== 核心分析 =====

    # 1. 总体统计
    print("【一、总体统计】")
    print(f"{'指标':>30} {'均值':>8} {'中位数':>8} {'标准差':>8} {'最小值':>8} {'最大值':>8} {'>0占比':>8}")
    print("-" * 80)
    for col, label in [('open_ret', '开盘卖回报(%)'), ('avg_trail_ret', '回落条件单卖(%)'),
                        ('trail_优势', '回落 - 开盘(ppt)')]:
        s = df_r[col]
        pos_pct = (s > 0).mean() * 100
        print(f"{label:>30} {s.mean():>8.2f} {s.median():>8.2f} {s.std():>8.2f} "
              f"{s.min():>8.2f} {s.max():>8.2f} {pos_pct:>7.1f}%")

    print()

    # 2. 按涨幅分层
    print("【二、按开盘涨幅分层】")
    df_r['开盘层'] = pd.cut(df_r['open_ret'],
        bins=[-float('inf'), -3, -1, 0, 1, 2, 3, 5, float('inf')],
        labels=['<-3%', '-3%~-1%', '-1%~0%', '0%~1%', '1%~2%', '2%~3%', '3%~5%', '>5%'])
    print(f"{'开盘涨幅':>12} {'样本数':>6} {'开盘卖均值':>10} {'回落均值':>10} {'回落优势(ppt)':>14}")
    print("-" * 60)
    for label, grp in df_r.groupby('开盘层', observed=True):
        print(f"{label:>12} {len(grp):>6} {grp['open_ret'].mean():>10.2f} "
              f"{grp['avg_trail_ret'].mean():>10.2f} "
              f"{(grp['avg_trail_ret'] - grp['open_ret']).mean():>+14.2f}")
    print()

    # 3. 阴线 vs 阳线路径差异（量化不确定性）
    print("【三、日内路径不确定性（阳线 vs 阴线假设的差异）】")
    df_r['路径差异'] = abs(df_r['阳线路径'] - df_r['阴线路径'])
    print(f"路径差异均值: {df_r['路径差异'].mean():.2f} ppt")
    print(f"路径差异中位数: {df_r['路径差异'].median():.2f} ppt")
    print(f"路径差异P90: {df_r['路径差异'].quantile(0.9):.2f} ppt")
    print()

    # 4. 什么时候回落单明显优于开盘卖？
    print("【四、回落单优势最大的场景（trail_优势 > 1ppt）】")
    best = df_r[df_r['trail_优势'] > 1].sort_values('trail_优势', ascending=False)
    print(f"共 {len(best)} 笔 (占 {len(best)/len(df_r)*100:.1f}%)")
    if len(best) > 0:
        print(best[['code', 'date', 'open_ret', 'avg_trail_ret', 'trail_优势', '阳线原因']].head(10).to_string())
    print()

    # 5. 什么时候回落单明显劣于开盘卖？
    print("【五、回落单劣势最大场景（trail_优势 < -1ppt）】")
    worst = df_r[df_r['trail_优势'] < -1].sort_values('trail_优势')
    print(f"共 {len(worst)} 笔 (占 {len(worst)/len(df_r)*100:.1f}%)")
    if len(worst) > 0:
        print(worst[['code', 'date', 'open_ret', 'avg_trail_ret', 'trail_优势', '阳线原因']].head(10).to_string())
    print()

    # 6. 关键分析：gap up场景（开盘涨0~3%，回落单是否有优势？）
    print("【六、关键场景：开盘涨0~3% 回落条件单vs开盘卖】")
    gap = df_r[(df_r['open_ret'] >= 0) & (df_r['open_ret'] <= 3)]
    if len(gap) > 0:
        print(f"样本数: {len(gap)}")
        print(f"  开盘卖均值: {gap['open_ret'].mean():.2f}%")
        print(f"  回落单均值: {gap['avg_trail_ret'].mean():.2f}%")
        print(f"  差值(回落-开盘): {gap['trail_优势'].mean():+.2f} ppt")

        # 找最优参数组合
        print(f"\n  最佳回落参数探索（当前激活={TRAILING_ACTIVATE}%, 回撤={TRAILING_DRAWDOWN}%）")
        print(f"  当前设定优于开盘卖的比例: {(gap['trail_优势'] > 0).mean()*100:.0f}%")

        # 按不同参数试
        best_params = None
        best_diff = -999
        for activate in [1.0, 1.5, 2.0]:
            for drawdown in [0.3, 0.5, 0.7, 1.0]:
                # 简化估算：开得越高、峰值越高，回撤触发概率越大
                # 这里用实际数据重跑太慢，用已有结果估算
                pass

    print()
    print("=" * 90)

    # 7. 最佳参数搜索
    print("【七、回落参数网格搜索（目标：最大化gap up场景收益）】")
    print(f"{'激活%':>6} {'回撤%':>6} {'样本数':>6} {'均值_开盘':>10} {'均值_回落':>10} {'优势':>8} {'优占比':>8}")
    print("-" * 60)

    # 对gap up场景重新搜索参数
    best_mean_diff = -999
    best_params = None
    for activate in [1.0, 1.2, 1.5, 2.0, 2.5, 3.0]:
        for drawdown in [0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5]:
            diffs = []
            trail_rets = []
            for _, trade in gap.iterrows():
                entry = trade['entry']
                res = simulate_trailing(entry, trade['open'], trade['high'],
                                         trade['low'], trade['close'], None,
                                         activate_pct=activate, drawdown_pct=drawdown)
                avg = np.mean([r['sell_pct'] for r in res])
                diffs.append(avg - trade['open_ret'])
                trail_rets.append(avg)
            if diffs:
                mean_diff = np.mean(diffs)
                pos_pct = np.mean([d > 0 for d in diffs]) * 100
                if mean_diff > 0.1 or True:  # 全部打印
                    print(f"{activate:>6.1f} {drawdown:>6.1f} {len(diffs):>6} "
                          f"{gap['open_ret'].mean():>10.2f} "
                          f"{np.mean(trail_rets):>10.2f} "
                          f"{mean_diff:>+8.2f} {pos_pct:>7.0f}%")
                if mean_diff > best_mean_diff:
                    best_mean_diff = mean_diff
                    best_params = (activate, drawdown)

    print()
    if best_params:
        print(f"最佳参数: 激活={best_params[0]}%, 回撤={best_params[1]}% "
              f"(平均提升 {best_mean_diff:+.2f}ppt)")
    else:
        print("无参数组合能提升开盘卖收益")


if __name__ == '__main__':
    main()
