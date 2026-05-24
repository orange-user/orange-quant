import sqlite3
import datetime
import numpy as np
import pandas as pd
import akshare as ak
from config import *


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS daily_data
                 (code TEXT, date TEXT, open REAL, close REAL, high REAL, low REAL, volume REAL, PRIMARY KEY(code,date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS factor_scores
                 (code TEXT, date TEXT, factor_name TEXT, score REAL, PRIMARY KEY(code,date,factor_name))''')
    c.execute('''CREATE TABLE IF NOT EXISTS factor_cache
                 (code TEXT, date TEXT, factor_name TEXT, value REAL, PRIMARY KEY(code, date, factor_name))''')
    c.execute('''CREATE TABLE IF NOT EXISTS factor_performance
                 (factor_name TEXT, date TEXT, ic REAL, rank_ic REAL, long_short_return REAL, PRIMARY KEY(factor_name, date))''')
    conn.commit()
    conn.close()


def get_stock_daily_cached(code, days=60):
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM daily_data WHERE code=? ORDER BY date DESC LIMIT ?", conn, params=(code, days))
        if len(df) >= days:
            df = df.sort_values('date')
            df['returns'] = df['close'].pct_change()
            df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
            df['ma5'] = df['close'].rolling(5).mean()
            df['ma20'] = df['close'].rolling(20).mean()
            return df[['date','open','close','high','low','volume','returns','volume_ratio','ma5','ma20']].dropna()
    except:
        pass
    finally:
        conn.close()

    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if len(df) < days:
            return None
        df = df.tail(days).copy()
        conn = sqlite3.connect(DB_PATH)
        for _, row in df.iterrows():
            try:
                conn.execute("INSERT OR REPLACE INTO daily_data VALUES (?,?,?,?,?,?,?)",
                             (code, str(row['日期']), float(row['开盘']), float(row['收盘']),
                              float(row['最高']), float(row['最低']), float(row['成交量'])))
            except:
                pass
        conn.commit()
        conn.close()
        df['returns'] = df['收盘'].pct_change()
        df['volume_ratio'] = df['成交量'] / df['成交量'].rolling(20).mean()
        df['ma5'] = df['收盘'].rolling(5).mean()
        df['ma20'] = df['收盘'].rolling(20).mean()
        return df[['日期','开盘','收盘','最高','最低','成交量','returns','volume_ratio','ma5','ma20']].dropna()
    except:
        return None


def get_index_daily(code="sh000300", days=60):
    try:
        df = ak.stock_zh_index_daily(symbol=code)
        df = df.tail(days).copy()
        df['returns'] = df['close'].pct_change()
        return df[['date','close','returns']].dropna()
    except:
        return None


def get_stock_info(code):
    try:
        df = ak.stock_individual_info_em(symbol=code)
        info = {}
        for _, row in df.iterrows():
            info[row['item']] = row['value']
        return info
    except:
        return {}


def get_stock_sector(code):
    try:
        df = ak.stock_board_concept_cons_em(symbol=code)
        return df['板块名称'].head(5).tolist() if df is not None and len(df) > 0 else []
    except:
        return []


def get_market_change():
    try:
        df = ak.stock_zh_index_daily(symbol="sh000001")
        if len(df) >= 1:
            t = df.tail(1)
            return float(t['close'].values[0]), float(t['pct_chg'].values[0]) if 'pct_chg' in df.columns else 0
    except:
        pass
    return 0, 0


def _get_pool_snapshot():
    """获取股票池快照（带重试，非交易日降级）"""
    for attempt in range(3):
        try:
            pool = ak.stock_zh_a_spot_em()
            if pool is not None and len(pool) > 100:
                return pool
        except Exception:
            if attempt < 2:
                import time
                time.sleep(2)
    return None


def _compute_factor_value(fname, meta, closes, opens, highs, lows, volumes, returns, info):
    formula = meta.get('formula')
    field = meta.get('field')

    if formula is None and field is not None:
        if field in ('close', 'open', 'high', 'low', 'volume'):
            return float({'close': closes, 'open': opens, 'high': highs, 'low': lows, 'volume': volumes}[field].iloc[-1])
        if field == 'turnover':
            return float(volumes.iloc[-5:].mean() / volumes.iloc[-20:].mean() if len(volumes) >= 20 else 1.0)
        if field == 'market_cap':
            raw = info.get('总市值', 0)
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace('亿','').replace('万','').replace(',','') or 0)
        if field == 'pe_ttm':
            raw = info.get('市盈率-动态', info.get('市盈率', 0))
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace(',','') or 0)
        if field == 'pb_ttm':
            raw = info.get('市净率', 0)
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace(',','') or 0)
        if field == 'ps_ttm':
            raw = info.get('市销率', 0)
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace(',','') or 0)
        if field == 'dividend_yield':
            raw = info.get('股息率', 0)
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace('%','').replace(',','') or 0)
        if field == 'roe':
            raw = info.get('净资产收益率', 0)
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace('%','').replace(',','') or 0)
        if field == 'roa':
            raw = info.get('总资产收益率', 0)
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace('%','').replace(',','') or 0)
        if field == 'gross_profit_margin':
            raw = info.get('毛利率', 0)
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace('%','').replace(',','') or 0)
        if field == 'net_profit_margin':
            raw = info.get('净利率', 0)
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace('%','').replace(',','') or 0)
        if field == 'revenue_yoy':
            raw = info.get('营业收入-同比增长', info.get('营业收入', 0))
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace('%','').replace(',','') or 0)
        if field == 'net_profit_yoy':
            raw = info.get('净利润-同比增长', 0)
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace('%','').replace(',','') or 0)
        if field == 'eps_yoy':
            raw = info.get('基本每股收益-同比增长', 0)
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).replace('%','').replace(',','') or 0)
        return 0.0

    if formula is not None:
        local_vars = {
            'close': closes, 'open': opens, 'high': highs, 'low': lows,
            'volume': volumes, 'returns': returns, 'np': np, 'pd': pd
        }
        try:
            result = eval(formula, {"__builtins__": {}}, local_vars)
            if isinstance(result, pd.Series):
                return float(result.iloc[-1])
            return float(result)
        except:
            return None

    return None


def load_factor_data(code):
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    try:
        cached = pd.read_sql_query(
            "SELECT factor_name, value FROM factor_cache WHERE code=? AND date=?",
            conn, params=(code, today_str))
        if len(cached) >= 10:
            conn.close()
            return dict(zip(cached['factor_name'], cached['value']))
    except:
        pass
    finally:
        try: conn.close()
        except: pass

    result = {}
    df = get_stock_daily_cached(code, 130)
    if df is None:
        return result

    closes = df['close'].astype(float)
    opens = df['open'].astype(float)
    highs = df['high'].astype(float)
    lows = df['low'].astype(float)
    volumes = df['volume'].astype(float)
    returns = closes.pct_change()

    info = {}
    try:
        info_raw = get_stock_info(code)
        info = {str(k): v for k, v in info_raw.items()}
    except:
        pass

    active_factors = {k: v for k, v in FACTOR_REGISTRY.items() if v.get('active')}
    for fname, fmeta in active_factors.items():
        try:
            val = _compute_factor_value(fname, fmeta, closes, opens, highs, lows, volumes, returns, info)
            if val is not None and np.isfinite(float(val)):
                result[fname] = round(float(val), 6)
        except:
            pass

    if result:
        conn = sqlite3.connect(DB_PATH)
        try:
            for fname, val in result.items():
                conn.execute("INSERT OR REPLACE INTO factor_cache VALUES (?,?,?,?)",
                             (code, today_str, fname, val))
            conn.commit()
        except:
            pass
        finally:
            conn.close()

    return result


def is_trading_day():
    """判断今天是否为交易日（简化版：周一至周五且非长假）"""
    wd = datetime.datetime.now().weekday()
    if wd >= 5:
        return False
    # 检查是否能获取到行情数据作为辅助判断
    try:
        df = ak.stock_zh_index_daily(symbol="sh000001")
        if df is not None and len(df) > 0:
            last_date = str(df['date'].values[-1])[:10]
            today = datetime.date.today().strftime('%Y-%m-%d')
            if last_date == today:
                return True
            # 如果最新数据日期不是今天（可能是还没收盘或非交易日）
            if wd == 0 and last_date < today:
                return True  # 周一可能有上周五数据，仍然可交易
    except:
        pass
    return wd < 5  # 默认周一到周五都是交易日


def refresh_all_daily_data():
    """后台刷新所有持仓股票的日线数据"""
    from config import load_json
    positions = load_json(POSITION_FILE, [])
    codes = set(p.get('code') for p in positions)
    refreshed = 0
    for code in codes:
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
            if df is not None and len(df) > 0:
                conn = sqlite3.connect(DB_PATH)
                for _, row in df.tail(30).iterrows():
                    try:
                        conn.execute("INSERT OR REPLACE INTO daily_data VALUES (?,?,?,?,?,?,?)",
                                     (code, str(row['日期']), float(row['开盘']), float(row['收盘']),
                                      float(row['最高']), float(row['最低']), float(row['成交量'])))
                    except:
                        pass
                conn.commit()
                conn.close()
                refreshed += 1
        except:
            pass
    return refreshed
