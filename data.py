import sqlite3
import datetime
import time as _time
import numpy as np
import pandas as pd
import akshare as ak
from config import *

# ==================== 进程内缓存 ====================
_pool_cache = {'data': None, 'time': 0}
_info_cache = {}
_sector_cache = {}
_news_cache = {'data': None, 'time': 0}
_index_cache = {}

def _cache_get(cache, key, ttl):
    entry = cache.get(key)
    if entry and (_time.time() - entry['time']) < ttl:
        return entry['data']
    return None

def _cache_set(cache, key, data):
    cache[key] = {'data': data, 'time': _time.time()}

def clear_caches():
    _pool_cache.clear(); _pool_cache['data'] = None; _pool_cache['time'] = 0
    _info_cache.clear(); _sector_cache.clear()
    _news_cache['data'] = None; _news_cache['time'] = 0
    _index_cache.clear()


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
    except Exception as e:
        logger.warning(f'get_stock_daily_cached({code}): SQLite cache miss or error: {e}')
        pass
    finally:
        conn.close()

    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if len(df) < days:
            return None
        df = df.tail(days).copy()
        df.columns = ['date', 'open', 'close', 'high', 'low', 'volume']
        df['date'] = df['date'].astype(str)
        conn = sqlite3.connect(DB_PATH)
        for _, row in df.iterrows():
            try:
                conn.execute("INSERT OR REPLACE INTO daily_data VALUES (?,?,?,?,?,?,?)",
                             (code, str(row['date']), float(row['open']), float(row['close']),
                              float(row['high']), float(row['low']), float(row['volume'])))
            except:
                pass
        conn.commit()
        conn.close()
        df['returns'] = df['close'].pct_change()
        df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        return df[['date','open','close','high','low','volume','returns','volume_ratio','ma5','ma20']].dropna()
    except Exception as e:
        logger.warning(f'get_stock_daily_cached({code}): primary fetch failed: {e}')

    # Fallback: Tencent history API (works even with future system dates)
    try:
        if code[0] in ('0', '2', '3'):
            tx_code = 'sz' + code
        elif code[0] in ('6', '9'):
            tx_code = 'sh' + code
        elif code[0] in ('4', '8'):
            tx_code = 'bj' + code
        else:
            tx_code = 'sz' + code

        # Limit to recent period for speed (1 year instead of full history)
        from datetime import date, timedelta
        end_d = date.today().strftime('%Y%m%d')
        start_d = (date.today() - timedelta(days=days + 30)).strftime('%Y%m%d')
        df = ak.stock_zh_a_hist_tx(symbol=tx_code, start_date=start_d, end_date=end_d)
        if df is None or len(df) < days:
            return None
        df = df.tail(days).copy()
        df.columns = ['date', 'open', 'close', 'high', 'low', 'volume']
        df['date'] = df['date'].astype(str)

        conn = sqlite3.connect(DB_PATH)
        for _, row in df.iterrows():
            try:
                conn.execute("INSERT OR REPLACE INTO daily_data VALUES (?,?,?,?,?,?,?)",
                             (code, str(row['date']), float(row['open']), float(row['close']),
                              float(row['high']), float(row['low']), float(row['volume'])))
            except:
                pass
        conn.commit()
        conn.close()

        df['returns'] = df['close'].pct_change()
        df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        return df[['date','open','close','high','low','volume','returns','volume_ratio','ma5','ma20']].dropna()
    except Exception as e:
        logger.warning(f'get_stock_daily_cached({code}): tencent_hist fallback failed: {e}')
        return None


def get_index_daily(code="sh000300", days=60):
    cache_key = f"{code}:{days}"
    cached = _cache_get(_index_cache, cache_key, 300)
    if cached is not None:
        return cached
    try:
        df = ak.stock_zh_index_daily(symbol=code)
        df = df.tail(days).copy()
        df['returns'] = df['close'].pct_change()
        result = df[['date','close','returns']].dropna()
        _cache_set(_index_cache, cache_key, result)
        return result
    except:
        return None


def get_stock_info(code):
    cached = _cache_get(_info_cache, code, 3600)
    if cached is not None:
        return cached
    try:
        df = ak.stock_individual_info_em(symbol=code)
        info = {}
        for _, row in df.iterrows():
            info[row['item']] = row['value']
        _cache_set(_info_cache, code, info)
        return info
    except:
        return {}


def get_stock_sector(code):
    cached = _cache_get(_sector_cache, code, 3600)
    if cached is not None:
        return cached
    try:
        df = ak.stock_board_concept_cons_em(symbol=code)
        sectors = df['板块名称'].head(5).tolist() if df is not None and len(df) > 0 else []
        _cache_set(_sector_cache, code, sectors)
        return sectors
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
    """获取股票池快照（多数据源fallback，含总市值）
    数据源优先级: Tencent批量 > EastMoney > 新浪自定义 > 新浪akshare
    缓存TTL: 30秒
    """
    global _pool_cache
    cached = _cache_get(_pool_cache, 'pool', 30)
    if cached is not None:
        return cached

    import re
    import requests as req
    from akshare.utils import demjson as _demjson

    # Source 1: Tencent batch quotes (most reliable, ~40-45s for all stocks)
    # Moved first because EastMoney/Sina block future dates (system date 2026)
    for attempt in range(2):
        try:
            all_codes = []
            # Get stock lists from exchange websites
            try:
                sh = ak.stock_info_sh_name_code(symbol="主板A股")
                all_codes.extend("sh" + c for c in sh["证券代码"].astype(str).str.zfill(6))
            except Exception:
                pass
            try:
                sz = ak.stock_info_sz_name_code(symbol="A股列表")
                all_codes.extend("sz" + c for c in sz["A股代码"].astype(str).str.zfill(6))
            except Exception:
                pass
            try:
                kcb = ak.stock_info_sh_name_code(symbol="科创板")
                all_codes.extend("sh" + c for c in kcb["证券代码"].astype(str).str.zfill(6))
            except Exception:
                pass
            try:
                bj = ak.stock_info_bj_name_code()
                all_codes.extend("bj" + c for c in bj["证券代码"].astype(str).str.zfill(6))
            except Exception:
                pass

            if len(all_codes) < 1000:
                raise ValueError(f"Too few codes from exchanges: {len(all_codes)}")

            # Fetch quotes in batches from Tencent
            results = []
            batch_size = 200
            for i in range(0, len(all_codes), batch_size):
                batch = all_codes[i:i + batch_size]
                url = "http://qt.gtimg.cn/q=" + ",".join(batch)
                r = req.get(url, timeout=15)
                for line in r.text.strip().split("\n"):
                    if '="' not in line:
                        continue
                    try:
                        data = line.split('="')[1].rstrip('";')
                        fields = data.split("~")
                        if len(fields) < 40:
                            continue
                        name = fields[1]
                        code = fields[2]
                        price = float(fields[3]) if fields[3] else 0
                        chg_pct = float(fields[32]) if fields[32] else 0
                        mktcap = float(fields[44]) * 1e8 if fields[44] else 1e12  # 亿元 -> 元
                        if price > 0 and code:
                            results.append({
                                "代码": code, "名称": name, "最新价": price,
                                "涨跌幅": chg_pct, "总市值": mktcap
                            })
                    except (ValueError, IndexError):
                        continue

            if len(results) > 500:
                df = pd.DataFrame(results)
                _pool_cache['data'] = df; _pool_cache['time'] = _time.time()
                return df
        except Exception:
            _time.sleep(2)

    # Source 2: EastMoney spot (fast when available, but blocks future dates)
    for attempt in range(1):
        try:
            pool = ak.stock_zh_a_spot_em()
            if pool is not None and len(pool) > 100:
                _pool_cache['data'] = pool; _pool_cache['time'] = _time.time()
                return pool
        except Exception:
            pass

    # Source 3: Custom Sina fetcher (preserves mktcap field)
    for attempt in range(2):
        try:
            count_url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount?node=hs_a"
            r = req.get(count_url, timeout=15)
            nums = re.findall(r"\d+", r.text)
            if not nums:
                raise ValueError(f"Count URL returned no numbers: {r.status_code}")
            stock_count = int(nums[0])
            if stock_count < 100:
                raise ValueError(f"Stock count too low: {stock_count}")
            page_count = stock_count // 80 + (1 if stock_count % 80 else 0)

            big_df = pd.DataFrame()
            sina_url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
            sina_payload = {"page": "1", "num": "80", "sort": "symbol", "asc": "1", "node": "hs_a", "symbol": "", "_s_r_a": "page"}

            for page in range(1, page_count + 1):
                sina_payload["page"] = str(page)
                r = req.get(sina_url, params=sina_payload, timeout=15)
                if not r.text or r.text.strip().startswith("<"):
                    raise ValueError(f"Page {page} returned HTML/error (status {r.status_code})")
                data_json = _demjson.decode(r.text)
                page_df = pd.DataFrame(data_json)
                big_df = pd.concat([big_df, page_df], ignore_index=True)
                if page % 10 == 0:
                    time.sleep(0.1)

            if len(big_df) < 100:
                raise ValueError(f"Too few records: {len(big_df)}")

            numeric_fields = ["trade", "pricechange", "changepercent", "buy", "sell",
                              "settlement", "open", "high", "low", "volume", "amount",
                              "per", "pb", "mktcap", "nmc", "turnoverratio"]
            for f in numeric_fields:
                if f in big_df.columns:
                    big_df[f] = pd.to_numeric(big_df[f], errors="coerce")

            result = pd.DataFrame()
            raw_codes = big_df.get("symbol", big_df.iloc[:, 0]).astype(str)
            result["代码"] = raw_codes.str.replace(r'^(sh|sz|bj)', '', regex=True)
            result["名称"] = big_df.get("name", big_df.iloc[:, 1])
            result["最新价"] = big_df.get("trade", 0)
            result["涨跌幅"] = big_df.get("changepercent", 0)
            result["总市值"] = big_df.get("mktcap", 1e6) * 1e4  # Sina returns 万元 -> 元
            _pool_cache['data'] = result; _pool_cache['time'] = _time.time()
            return result

        except Exception:
            _time.sleep(3 * (attempt + 1))

    # Source 4: akshare Sina spot (no mktcap column - use default)
    for attempt in range(2):
        try:
            pool = ak.stock_zh_a_spot()
            if pool is not None and len(pool) > 100:
                if "总市值" not in pool.columns:
                    pool["总市值"] = 1e12
                pool["代码"] = pool["代码"].astype(str).str.replace(r'^(sh|sz|bj)', '', regex=True)
                _pool_cache['data'] = pool; _pool_cache['time'] = _time.time()
                return pool
        except Exception:
            _time.sleep(2)

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


def get_global_news_cached():
    """获取缓存的市场新闻，TTL 300秒"""
    if _news_cache['data'] and (_time.time() - _news_cache['time']) < 300:
        return _news_cache['data']
    try:
        df = ak.stock_info_global_em()
        if df is not None:
            _news_cache['data'] = df
            _news_cache['time'] = _time.time()
        return df
    except:
        return _news_cache['data']


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
            df = get_stock_daily_cached(code, 130)
            if df is not None and len(df) > 0:
                refreshed += 1
        except:
            pass
    return refreshed


def batch_warm_cache(codes, days=60, max_workers=16):
    """批量预热日线缓存：并行获取多只股票的日线数据，填满SQLite缓存"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    warmed = 0
    failed = 0
    total = len(codes)

    # 先检查已有缓存，跳过已缓存的股票
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    try:
        cached_df = pd.read_sql_query(
            "SELECT code, COUNT(*) as cnt FROM daily_data GROUP BY code", conn)
        cached_set = set(cached_df[cached_df['cnt'] >= days]['code'].tolist())
    except:
        cached_set = set()
    finally:
        conn.close()

    uncached = [c for c in codes if c not in cached_set]
    already = total - len(uncached)

    if not uncached:
        return {'warmed': 0, 'failed': 0, 'already_cached': already, 'total': total}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(get_stock_daily_cached, c, days): c for c in uncached}
        for fut in as_completed(futures):
            try:
                df = fut.result()
                if df is not None and len(df) >= days:
                    warmed += 1
                else:
                    failed += 1
            except:
                failed += 1

    return {'warmed': warmed, 'failed': failed, 'already_cached': already, 'total': total}
