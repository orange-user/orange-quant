"""D1+D2策略净值曲线（测试集，最贴近真实）"""
import sys, os, json, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3, numpy as np, pandas as pd
from config import DB_PATH
from backtest.backtest_d1d2_v2 import run_backtest

# ===== 参数 =====
INITIAL_CAPITAL = 100000
MAX_POSITIONS_PER_DAY = 3    # 每天最多开3笔
POSITION_SIZE_PCT = 0.30     # 每笔30%仓位
SLIPPAGE = 0.001              # 滑点0.1%

# 最佳参数（从训练集选出）
D1_MIN_CHG = 4
D1_MIN_VR = 1.2
D2_MAX_OPEN = -1.0

# ===== 获取数据 =====
conn = sqlite3.connect(DB_PATH)
all_codes = [r[0] for r in conn.execute(
    "SELECT code FROM daily_data GROUP BY code HAVING COUNT(*) > 40").fetchall()]
conn.close()
print(f'Pool: {len(all_codes)} stocks')

# 训练/测试分离（同回测）
np.random.seed(42)
shuffled = np.random.permutation(all_codes).tolist()
split = len(shuffled) // 2
train_codes = shuffled[:split]
test_codes = shuffled[split:split+500]
print(f'Train: {len(train_codes)}, Test: {len(test_codes)}')

# 只用测试集做净值曲线
trades = run_backtest(test_codes, d1_min_chg=D1_MIN_CHG, d1_min_vr=D1_MIN_VR,
                      d2_max_open=D2_MAX_OPEN, max_hold=3)
t1 = [t for t in trades if t['hold_days'] == 1]
print(f'Test set T+1 trades: {len(t1)}')
print(f'T+1 avg return: {np.mean([t["pnl_pct"] for t in t1]):+.2f}%')
print(f'T+1 winrate: {sum(1 for t in t1 if t["is_win"])/len(t1)*100:.1f}%')

df = pd.DataFrame(t1)
df['date'] = pd.to_datetime(df['entry_date'])
df = df.sort_values('date')

dates = sorted(df['date'].unique())
print(f'\nTrading days: {len(dates)}')

# 模拟实仓
capital = INITIAL_CAPITAL
position_size = capital * POSITION_SIZE_PCT
equity_curve = []
max_equity = capital
trades_today = 0
last_date = None
active_positions = []

for date in dates:
    day_trades = df[df['date'] == date]

    for _, t in day_trades.iterrows():
        if len(active_positions) >= MAX_POSITIONS_PER_DAY:
            break

        # 检查可用资金
        used = sum(p['cost'] for p in active_positions)
        available = capital - used
        actual_size = min(position_size, available * 0.95)

        if actual_size < 1000:  # 最少1000元
            continue

        # 买入
        if t['pnl_pct'] > 0:
            cost = actual_size
            pnl = cost * t['pnl_pct'] / 100
            exit_pnl = cost * t['pnl_pct'] / 100
        else:
            cost = actual_size
            pnl = cost * t['pnl_pct'] / 100
            exit_pnl = cost * t['pnl_pct'] / 100

        active_positions.append({
            'entry_date': date,
            'cost': cost,
            'pnl': pnl,
            'pnl_pct': t['pnl_pct'],
        })

    # 每日结算
    day_pnl = sum(p['pnl'] for p in active_positions)
    capital += day_pnl
    position_size = capital * POSITION_SIZE_PCT
    max_equity = max(max_equity, capital)
    drawdown = (capital - max_equity) / max_equity * 100

    equity_curve.append({
        'date': str(date)[:10],
        'capital': round(capital, 2),
        'day_pnl': round(day_pnl, 2),
        'positions': len(active_positions),
        'drawdown': round(drawdown, 2),
    })

    # T+1天可以卖（A股T+1），这里简化：持仓1天后释放资金，位置保留到第二天
    # 更新active_positions：移除昨天的（T+0已结算）
    active_positions = []  # T+0当日结算，持仓清空

# ===== 输出 =====
ec_df = pd.DataFrame(equity_curve)
total_return = (capital / INITIAL_CAPITAL - 1) * 100
trading_months = max(1, len(dates) / 21)
monthly_return = ((capital / INITIAL_CAPITAL) ** (1 / trading_months) - 1) * 100
sharpe_ratio = np.mean(ec_df['day_pnl']) / max(np.std(ec_df['day_pnl']), 0.01) * np.sqrt(252)
max_dd = ec_df['drawdown'].min()

print(f'\n{"="*55}')
print(f'  Equity Curve - Test Set Only')
print(f'  Initial: {INITIAL_CAPITAL:,}')
print(f'  Final:   {capital:,.0f}')
print(f'  Total Return: +{total_return:.1f}%')
print(f'  Trading Months: {trading_months:.1f}')
print(f'  Monthly Return: {monthly_return:+.1f}%')
print(f'  Annualized (x12): {monthly_return*12:+.1f}%')
print(f'  Max Drawdown: {max_dd:.1f}%')
print(f'  Sharpe (ann.): {sharpe_ratio:.2f}')
print(f'  Total Trades: {len(t1)}')
print(f'  Winrate: {sum(1 for t in t1 if t["is_win"])/len(t1)*100:.1f}%')
print(f'{"="*55}')

# 按月明细
ec_df['month'] = ec_df['date'].str[:7]
monthly = ec_df.groupby('month').agg(
    days=('date', 'count'),
    trades=('positions', 'sum'),
    total_pnl=('day_pnl', 'sum'),
    end_capital=('capital', 'last'),
).round(2)
monthly['return_pct'] = monthly['total_pnl'] / (monthly['end_capital'] - monthly['total_pnl']) * 100
monthly['return_pct'] = monthly['return_pct'].round(2)
print(f'\nMonthly Detail:')
print(f'  {"Month":<10} {"Days":>5} {"Trades":>7} {"Pct":>8} {"Capital":>12}')
print(f'  {"-"*42}')
for idx, row in monthly.iterrows():
    print(f'  {idx:<10} {row["days"]:>5} {row["trades"]:>7.0f} {row["return_pct"]:>7.1f}% {row["end_capital"]:>10,.0f}')
print(f'  {"-"*42}')
print(f'  {"Total":<10} {"":>5} {ec_df["positions"].sum():>7.0f} {total_return:>7.1f}% {capital:>10,.0f}')

# 每周净值输出（给前端）
ec_df.to_csv(os.path.join(os.path.dirname(__file__), 'equity_curve.csv'), index=False)
print(f'\nEquity curve saved to equity_curve.csv')

# 打印每周净值关键点
print(f'\nKey Points:')
for _, r in ec_df.iterrows():
    if r['date'].endswith(('-01', '-15')) or r.name == ec_df.index[-1]:
        print(f'  {r["date"]}: {r["capital"]:,.0f} (DD: {r["drawdown"]:.1f}%)')
