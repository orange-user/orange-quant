"""
回测：MaxProfitExecutor vs 等权持有TopN（橙卫基线）
"""
import sys, os, json, pickle, sqlite3, warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, DATA_DIR
from executor import MaxProfitExecutor
from probability_matrix_v7 import get_realtime_signal

warnings.filterwarnings('ignore')
np.seterr(all='ignore')

SLIPPAGE = 0.001; COMM_RATE = 0.00025; MIN_COMM = 5.0; STAMP_RATE = 0.001

def trade_cost(price, shares, is_buy):
    val = price * shares
    comm = max(val * COMM_RATE, MIN_COMM)
    stamp = 0 if is_buy else val * STAMP_RATE
    return val + comm + stamp if is_buy else val - comm - stamp

def load_all_daily(top_n=500):
    conn = sqlite3.connect(DB_PATH)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM daily_data WHERE adjust='qfq'").fetchall()]
    valid = [c for c in codes if c.startswith(('0','3','6'))
             and not c.startswith(('300','301','688','689','8','4'))]
    valid = valid[:top_n]
    placeholders = ','.join(['?']*len(valid))
    df = pd.read_sql(
        f"SELECT code, date, open, close, high, low, volume FROM daily_data "
        f"WHERE adjust='qfq' AND code IN ({placeholders}) ORDER BY code, date",
        conn, params=valid
    )
    conn.close()
    for c in ['open','close','high','low','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['open','close','high','low','volume'])
    df['date'] = df['date'].astype(str)
    return df, valid

def load_index():
    idx_path = os.path.join(DATA_DIR, 'index_cache.json')
    if not os.path.exists(idx_path):
        return None
    with open(idx_path) as f:
        raw = json.load(f)
    dates = raw.get('date', [])
    closes = raw.get('close', [])
    if not dates or not closes:
        return None
    idx = pd.DataFrame({'date': dates, 'close': closes})
    idx['close'] = pd.to_numeric(idx['close'], errors='coerce')
    idx = idx.dropna()
    idx['date'] = idx['date'].astype(str)
    return idx

def calc_features(group):
    g = group.sort_values('date').reset_index(drop=True)
    c = g['close']; h = g['high']; l = g['low']
    delta = c.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_g = gain.rolling(14).mean()
    avg_l = loss.rolling(14).mean()
    g['rsi_14'] = 100 - (100 / (1 + avg_g / avg_l.replace(0, np.nan)))
    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    g['macd_dif'] = ema12 - ema26
    low9 = l.rolling(9).min()
    high9 = h.rolling(9).max()
    rsv = (c - low9) / (high9 - low9).replace(0, np.nan) * 100
    k = rsv.ewm(com=2).mean()
    d = k.ewm(com=2).mean()
    g['kdj_j'] = 3 * k - 2 * d
    g['momentum_20d'] = c.pct_change(20) * 100
    return g

def get_pm_signals(df, pm_model, min_count=50):
    features = pm_model['features']
    bin_edges = pm_model['bin_edges']
    pm = pm_model['pm']
    signals = []
    for _, row in df.iterrows():
        latest = pd.Series({
            'feat_limit_state': 0,
            'macd_dif': row['macd_dif'],
            'rsi_14': row['rsi_14'],
            'kdj_j': row['kdj_j'],
        })
        info = get_realtime_signal(latest, bin_edges, pm, features, min_count)
        signals.append(info.get('signal', 0))
    df['pm_signal'] = signals
    return df

def calc_market_scale(idx, date_str):
    idx = idx[idx['date'] <= date_str].sort_values('date')
    if len(idx) < 30:
        return 1.0, 1, 0
    c = idx['close'].values
    ma10 = pd.Series(c).rolling(10).mean().iloc[-1]
    ma30 = pd.Series(c).rolling(30).mean().iloc[-1]
    ma_cross = 1 if ma10 > ma30 else 0
    recent3 = c[-3:]
    con_drop = 0
    if len(recent3) == 3:
        if all(recent3[i] > recent3[i+1] for i in range(2)):
            con_drop = 3
    ret = pd.Series(c).pct_change().dropna().tail(20)
    ann_vol = ret.std() * (250 ** 0.5) if len(ret) > 0 else 0.2
    vol_scale = min(1.0, max(0.3, 0.15 / ann_vol)) if ann_vol > 0 else 1.0
    scale = vol_scale
    if ma_cross == 0:
        scale *= 0.3
    return scale, ma_cross, con_drop

class BaselineTopN:
    def __init__(self, n=5, capital=100000):
        self.n = n
        self.holdings = {}
        self.capital = capital
        self.cash = capital
        self.trades = []
        self.equity = []

    def daily(self, candidates_df, date_str):
        top = candidates_df.nlargest(self.n, 'pm_signal')
        top_codes = set(top['code'].tolist())
        for code in list(self.holdings.keys()):
            if code not in top_codes:
                h = self.holdings.pop(code)
                row = top[top['code'] == code]
                price = float(row.iloc[0]['close']) if not row.empty else None
                if price is None:
                    continue
                proceeds = trade_cost(price, h['shares'], is_buy=False)
                self.cash += proceeds
                self.trades.append({'code': code, 'action': 'SELL', 'price': price,
                                    'shares': h['shares'], 'pnl': (price/h['buy_price']-1)*100, 'date': date_str})
        for _, row in top.iterrows():
            code = row['code']
            if code in self.holdings:
                continue
            price = float(row['close'])
            if price <= 0 or self.cash < price * 100:
                continue
            remaining = self.n - len(self.holdings)
            if remaining <= 0:
                break
            alloc = self.cash * 0.8 / remaining
            shares = int(alloc / price / 100) * 100
            if shares < 100:
                continue
            cost = trade_cost(price, shares, is_buy=True)
            if cost > self.cash:
                shares = int(self.cash * 0.8 / price / 100) * 100
                cost = trade_cost(price, shares, is_buy=True)
            self.cash -= cost
            self.holdings[code] = {'shares': shares, 'buy_price': price}
            self.trades.append({'code': code, 'action': 'BUY', 'price': price, 'shares': shares, 'date': date_str})
        val = self.cash
        for code, h in self.holdings.items():
            row = top[top['code'] == code]
            p = float(row.iloc[0]['close']) if not row.empty else h['buy_price']
            val += h['shares'] * p
        self.equity.append({'date': date_str, 'equity': val})
        return val

def run_backtest(start_date='2025-01-01', end_date='2026-06-01', capital=100000):
    print(f"回测: {start_date} ~ {end_date}, 资金 {capital:,.0f}")

    df, codes = load_all_daily(500)
    idx = load_index()
    if idx is None:
        print("X 无指数数据")
        return

    model_path = os.path.join(DATA_DIR, 'pm_risk_model.pkl')
    if not os.path.exists(model_path):
        print("X 无PM模型")
        return
    with open(model_path, 'rb') as f:
        pm_model = pickle.load(f)

    print(f"股票: {len(codes)}只, 数据: {len(df):,}行")

    # 逐只计算特征
    print("计算特征+PM信号...")
    all_data = []
    for ci, code in enumerate(codes):
        chunk = df[df['code'] == code].copy()
        if len(chunk) < 30:
            continue
        chunk = calc_features(chunk)
        chunk = chunk.dropna(subset=['rsi_14', 'macd_dif', 'kdj_j', 'momentum_20d'])
        if chunk.empty:
            continue
        chunk = get_pm_signals(chunk, pm_model)
        chunk['code'] = code
        all_data.append(chunk)
        if (ci+1) % 100 == 0:
            print(f"  {ci+1}/{len(codes)}")

    if not all_data:
        print("X 无有效数据")
        return
    df = pd.concat(all_data, ignore_index=True)
    df = df[(df['date'] >= start_date) & (df['date'] <= end_date)]
    df = df.dropna(subset=['pm_signal'])
    trading_dates = sorted(df['date'].unique())
    print(f"交易日: {len(trading_dates)}天, 有效行: {len(df):,}")

    # 基线
    baseline = BaselineTopN(5, capital)
    for di, date_str in enumerate(trading_dates):
        if (di+1) % 100 == 0:
            print(f"  基线 {di+1}/{len(trading_dates)}")
        day_df = df[df['date'] == date_str].copy()
        if not day_df.empty:
            baseline.daily(day_df, date_str)

    if not baseline.equity:
        print("X 基线无交易")
        return

    bf = baseline.equity[-1]['equity']
    bt = (bf / capital - 1) * 100
    eq = pd.Series([e['equity'] for e in baseline.equity])
    br = eq.pct_change().dropna()
    bs = br.mean() / br.std() * (250 ** 0.5) if br.std() > 0 else 0
    bw = sum(1 for t in baseline.trades if t.get('pnl', 0) > 0) if baseline.trades else 0
    bwr = bw / len(baseline.trades) * 100 if baseline.trades else 0
    peak = eq.expanding().max()
    bmdd = ((eq - peak) / peak * 100).min()

    print("\n" + "=" * 50)
    print("  基线：等权 Top5")
    print("=" * 50)
    print(f"  总收益:    {bt:+.2f}%")
    print(f"  最终权益:  {bf:,.0f}")
    print(f"  夏普:      {bs:.3f}")
    print(f"  最大回撤:  {bmdd:.2f}%")
    print(f"  交易:      {len(baseline.trades)}笔")
    print(f"  胜率:      {bwr:.1f}%")

if __name__ == '__main__':
    run_backtest()
