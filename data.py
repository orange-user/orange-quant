import sqlite3
import datetime
import sys
import os
import time as _time
import numpy as np
import pandas as pd
import akshare as ak
from config import *

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
os.makedirs(CACHE_DIR, exist_ok=True)
_CSV_CACHE_TTL = 86400 * 7  # CSV缓存保留7天

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

# ==================== CSV多级缓存 ====================
def _csv_cache_path(code):
    return os.path.join(CACHE_DIR, f'{code}.csv')

def _csv_cache_save(code, df):
    """保存到CSV缓存 (存60天数据)"""
    try:
        path = _csv_cache_path(code)
        df.tail(60).to_csv(path, index=False)
    except Exception:
        pass

def _csv_cache_load(code, days=60):
    """从CSV缓存读取"""
    try:
        path = _csv_cache_path(code)
        if not os.path.exists(path):
            return None
        age = _time.time() - os.path.getmtime(path)
        if age > _CSV_CACHE_TTL:
            return None
        df = pd.read_csv(path)
        if len(df) >= days:
            return df.tail(days)
        return None
    except Exception:
        return None


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

    # Try wudao-stock MCP (high-quality kline, but limited to 50 calls/day)
    try:
        from wudao_client import kline as wudao_kline
        result = wudao_kline([code], days)
        if result and result.get('content'):
            # Parse MCP response - find structured data
            sc = result.get('structuredContent') or {}
            items = None
            if sc.get('data', {}).get('batch'):
                items = sc['data'].get('items', [])
            elif sc.get('data', {}).get('rows'):
                items = [sc['data']]
            if items:
                for item in items:
                    klines = item.get('rows', [])
                    if not klines or len(klines) < days * 0.5:
                        continue
                    rows = []
                    for k in klines:
                        rows.append({
                            'date': str(k.get('date', k.get('day', ''))).replace('-', ''),
                            'open': float(k.get('open', 0)),
                            'close': float(k.get('close', 0)),
                            'high': float(k.get('high', 0)),
                            'low': float(k.get('low', 0)),
                            'volume': float(k.get('volume', 0)),
                        })
                    if not rows:
                        continue
                    df = pd.DataFrame(rows).tail(days)
                    conn = sqlite3.connect(DB_PATH)
                    for _, row in df.iterrows():
                        try:
                            conn.execute("INSERT OR REPLACE INTO daily_data VALUES (?,?,?,?,?,?,?)",
                                         (code, str(row['date']), float(row['open']), float(row['close']),
                                          float(row['high']), float(row['low']), float(row['volume'])))
                        except: pass
                    conn.commit()
                    conn.close()
                    df['returns'] = df['close'].pct_change()
                    df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
                    df['ma5'] = df['close'].rolling(5).mean()
                    df['ma20'] = df['close'].rolling(20).mean()
                    logger.info(f'wudao_kline({code}): {len(df)}条')
                    return df[['date','open','close','high','low','volume','returns','volume_ratio','ma5','ma20']].dropna()
    except Exception as e:
        logger.debug(f'wudao_kline({code}): {e}')

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
        # 保存到CSV缓存
        _csv_cache_save(code, df)
        return df[['date','open','close','high','low','volume','returns','volume_ratio','ma5','ma20']].dropna()
    except Exception as e:
        logger.warning(f'get_stock_daily_cached({code}): primary fetch failed: {e}')

    # Fallback: CSV缓存 (akshare失败时读本地)
    try:
        df = _csv_cache_load(code, days)
        if df is not None:
            df['returns'] = df['close'].pct_change()
            df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
            df['ma5'] = df['close'].rolling(5).mean()
            df['ma20'] = df['close'].rolling(20).mean()
            return df[['date','open','close','high','low','volume','returns','volume_ratio','ma5','ma20']].dropna()
    except Exception as e:
        logger.warning(f'get_stock_daily_cached({code}): CSV cache failed: {e}')

    # Fallback: adata (when akshare is down)
    try:
        sys.path.insert(0, r'C:\Users\Administrator\Desktop\adata')
        from adata.stock.market.stock_market.stock_market import StockMarket
        m = StockMarket()
        result = m.get_market(code, start_date=(
            datetime.datetime.now() - datetime.timedelta(days=days+10)).strftime('%Y-%m-%d'),
            end_date=datetime.datetime.now().strftime('%Y-%m-%d'))
        if hasattr(result, 'to_dict') and len(result) >= days:
            df = result.tail(days).copy()
            df = df.rename(columns={'trade_date': 'date'})
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
    except Exception as e2:
        logger.warning(f'get_stock_daily_cached({code}): adata fallback failed: {e2}')

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
    # Tencent API for index data (akshare hangs)
    try:
        _raw_code = code.replace('sh','').replace('sz','')
        _pre = 'sz' if '399' in _raw_code else 'sh'
        _idx_url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={_pre}{_raw_code},day,,,{days},qfq'
        import httpx as _h
        _resp = _h.get(_idx_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        if _resp.status_code == 200:
            _j = _resp.json()
            _qfq = _j.get('data', {}).get(f'{_pre}{_raw_code}', {}).get('qfqday', [])
            if _qfq:
                _rows = [{'date': str(l[0]).replace('-',''), 'close': float(l[2])} for l in _qfq if len(l) >= 3]
                _df = pd.DataFrame(_rows).tail(days)
                _df['returns'] = _df['close'].pct_change()
                _cache_set(_index_cache, cache_key, _df)
                return _df
    except Exception:
        pass
    try:
        df = ak.stock_zh_index_daily(symbol=code)
        df = df.tail(days).copy()
        df['returns'] = df['close'].pct_change()
        result = df[['date','close','returns']].dropna()
        _cache_set(_index_cache, cache_key, result)
        return result
    except:
        return None


# ── 分钟K线（Sina API）──

_sina_min_cache = {}


def get_minute_kline(code, scale=15, bars=96):
    """获取分钟K线（Sina API）

    scale: 5/15/30/60 分钟
    bars: 返回K线数量（最多约200）
    返回 DataFrame[date, open, close, high, low, volume]
    """
    cache_key = f'{code}:{scale}:{bars}'
    cached = _cache_get(_sina_min_cache, cache_key, 30)  # 缓存30秒
    if cached is not None:
        return cached

    import httpx as _h
    prefix = 'sh' if code.startswith(('6', '9')) else 'sz'
    url = (
        f'http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
        f'CN_MarketData.getKLineData?symbol={prefix}{code}&scale={scale}&datalen={bars}'
    )
    try:
        r = _h.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data or not isinstance(data, list):
            return None
        rows = []
        for d in data:
            rows.append({
                'date': d['day'],
                'open': float(d['open']),
                'close': float(d['close']),
                'high': float(d['high']),
                'low': float(d['low']),
                'volume': float(d['volume']),
            })
        df = pd.DataFrame(rows)
        _cache_set(_sina_min_cache, cache_key, df)
        return df
    except Exception as e:
        logger.debug(f'get_minute_kline({code}): {e}')
        return None


def get_mtf_kline(code):
    """多时间框架K线获取（日线+60分+15分+5分）

    返回 {daily, hour, quarter, five} → DataFrame
    用于多时间框架形态识别和信号合成
    """
    result = {}

    daily = get_stock_daily_cached(code, 60)
    if daily is not None and len(daily) > 10:
        result['daily'] = daily

    hour = get_minute_kline(code, scale=60, bars=48)
    if hour is not None and len(hour) > 5:
        result['hour'] = hour

    quarter = get_minute_kline(code, scale=15, bars=48)
    if quarter is not None and len(quarter) > 5:
        result['quarter'] = quarter

    five = get_minute_kline(code, scale=5, bars=48)
    if five is not None and len(five) > 5:
        result['five'] = five

    return result


def _request_with_timeout(func, *args, timeout=8):
    """带超时的请求包装器，超时返回None"""
    import threading
    result = [None]
    exc = [None]
    def runner():
        try:
            result[0] = func(*args)
        except Exception as e:
            exc[0] = e
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    if exc[0] is not None:
        raise exc[0]
    return result[0]


def get_stock_info(code):
    """获取股票基本面（优先pool缓存，回退wudao批量，最后akshare）"""
    cached = _cache_get(_info_cache, code, 3600)
    if cached is not None:
        return cached
    # 从pool缓存读取（wudao stock_screener已包含基本面）
    global _pool_cache
    pool_df = _cache_get(_pool_cache, 'pool', 30)
    if pool_df is not None and code in pool_df['代码'].values:
        row = pool_df[pool_df['代码'] == code].iloc[0]
        info = {}
        if '量比' in pool_df.columns:
            info['量比'] = row.get('量比', 1)
        _cache_set(_info_cache, code, info)
        return info
    try:
        df = _request_with_timeout(ak.stock_individual_info_em, symbol=code)
        if df is None:
            return {}
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
        df = _request_with_timeout(ak.stock_board_concept_cons_em, symbol=code)
        if df is None:
            return []
        sectors = df['板块名称'].head(5).tolist() if df is not None and len(df) > 0 else []
        _cache_set(_sector_cache, code, sectors)
        return sectors
    except:
        return []


def get_market_change():
    """大盘涨跌（优先wudao market_overview，回退akshare）"""
    try:
        from wudao_client import market_overview as wudao_market
        r = wudao_market()
        if r:
            # 从文本中解析大盘数据
            import re
            m = re.search(r'[-+]?\d+\.?\d*', r)
            if m:
                # 简单返回，主要用akshare的精确数据
                pass
    except:
        pass
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

    # Source 0: wudao-stock MCP stock_screener (最快，1次调用)
    try:
        from wudao_client import stock_screener as wudao_screener
        result = wudao_screener(
            market_cap_min_yi=50, price_min=3,
            market='main', exclude_st=True,
            limit=300, sort_by='volumeRatio', sort_order='desc',
        )
        if result:
            sc = result.get('structuredContent') or {}
            rows = sc.get('data', {}).get('rows', [])
            if rows and len(rows) > 100:
                results = []
                for item in rows:
                    code = str(item.get('code', ''))
                    if code.startswith(('688', '689', '300', '301')):
                        continue
                    results.append({
                        '代码': code,
                        '名称': item.get('name', ''),
                        '最新价': float(item.get('close', 0)),
                        '涨跌幅': float(item.get('closePctChg', 0)),
                        '总市值': float(item.get('totalMarketCapYi', 0)) * 1e8,
                        '量比': float(item.get('volumeRatio', 1)),
                        '行业': item.get('industry', ''),
                        'pe': item.get('peTtm', 0),
                        'pb': item.get('pb', 0),
                        '昨收': float(item.get('preClose', 0)),
                        'ma5': float(item.get('ma5', 0)) if item.get('ma5') else 0,
                        'ma10': float(item.get('ma10', 0)) if item.get('ma10') else 0,
                        'ma20': float(item.get('ma20', 0)) if item.get('ma20') else 0,
                    })
                if len(results) > 200:
                    df = pd.DataFrame(results)
                    _pool_cache['data'] = df; _pool_cache['time'] = _time.time()
                    logger.info(f'_get_pool_snapshot: wudao {len(results)} stocks')
                    return df
                else:
                    logger.info(f'_get_pool_snapshot: wudao returned {len(rows)} rows (<200), fallback')
    except Exception as e:
        logger.debug(f'_get_pool_snapshot: wudao skipped: {e}')

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
                        yclose = float(fields[4]) if fields[4] else 0
                        chg_pct = float(fields[32]) if fields[32] else 0
                        day_high = float(fields[33]) if fields[33] else 0
                        day_low = float(fields[34]) if fields[34] else 0
                        mktcap = float(fields[44]) * 1e8 if fields[44] else 1e12  # 亿元 -> 元
                        if price > 0 and code:
                            results.append({
                                "代码": code, "名称": name, "最新价": price,
                                "涨跌幅": chg_pct, "总市值": mktcap,
                                "昨收": yclose, "今日最高": day_high, "今日最低": day_low
                            })
                    except (ValueError, IndexError):
                        continue

            if len(results) > 500:
                df = pd.DataFrame(results)
                df = df[~df['代码'].astype(str).str.startswith(('688', '689', '300', '301'))]
                _pool_cache['data'] = df; _pool_cache['time'] = _time.time()
                return df
        except Exception:
            _time.sleep(2)

    # Source 2: EastMoney spot (fast when available, but blocks future dates)
    for attempt in range(1):
        try:
            pool = ak.stock_zh_a_spot_em()
            if pool is not None and len(pool) > 100:
                pool = pool[~pool['代码'].astype(str).str.startswith(('688', '689', '300', '301'))]
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
            result = result[~result['代码'].astype(str).str.startswith(('688', '689', '300', '301'))]
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
                pool = pool[~pool['代码'].astype(str).str.startswith(('688', '689', '300', '301'))]
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
        # 预计算常用技术指标，供formula eval使用
        ma5 = closes.rolling(5).mean()
        ma10 = closes.rolling(10).mean()
        ma20 = closes.rolling(20).mean()
        ma60 = closes.rolling(60).mean() if len(closes) >= 60 else ma20
        # 布林带
        rm = closes.rolling(20).mean()
        rs = closes.rolling(20).std()
        upper = rm + 2 * rs
        lower = rm - 2 * rs
        # True Range (ATR用)
        tr = pd.concat([
            highs - lows,
            (highs - closes.shift(1)).abs(),
            (lows - closes.shift(1)).abs()
        ], axis=1).max(axis=1)
        # KDJ
        lowest = lows.rolling(9).min()
        highest = highs.rolling(9).max()
        rsv = (closes - lowest) / (highest - lowest).replace(0, np.nan) * 100
        k_line = rsv.ewm(com=2).mean()
        d_line = k_line.ewm(com=2).mean()
        j_line = 3 * k_line - 2 * d_line

        # 辅助函数（闭包自动捕获上面的 closes/volumes 等变量）
        def _rsi(period=14):
            delta = closes.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_g = gain.rolling(period).mean()
            avg_l = loss.rolling(period).mean()
            rs = avg_g / avg_l.replace(0, np.nan)
            return 100 - (100 / (1 + rs))

        def _ema(period):
            return closes.ewm(span=period, adjust=False).mean()

        local_vars = {
            'close': closes, 'open': opens, 'high': highs, 'low': lows,
            'volume': volumes, 'returns': returns,
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'upper': upper, 'lower': lower, 'rm': rm, 'rs_std': rs,
            'tr': tr,
            'k': k_line, 'd': d_line, 'j': j_line,
            'rsi': _rsi, 'ema': _ema,
            'np': np, 'pd': pd
        }
        try:
            result = eval(formula, {"__builtins__": {}}, local_vars)
            if isinstance(result, pd.Series):
                return float(result.iloc[-1])
            return float(result)
        except Exception:
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
    """判断今天是否为交易日（优先wudao，回退原逻辑）"""
    try:
        from wudao_client import trading_calendar
        r = trading_calendar()
        if r and ('交易日' in str(r) or '是' in str(r)):
            return '不是' not in str(r)
    except Exception:
        pass
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


def batch_get_stock_info(codes):
    """批量获取股票基本面（用wudao valuation_snapshot，最多20只/次）"""
    try:
        from wudao_client import valuation_snapshot as wudao_val
        result = wudao_val(codes[:20])
        if result:
            sc = result.get('structuredContent') or {}
            items = sc.get('data', {}).get('items', [])
            for item in items:
                stock = item.get('stock', {})
                code = stock.get('code', '')
                if not code:
                    continue
                info = {
                    '市盈率-动态': item.get('peTtm', 0),
                    '市净率': item.get('pb', 0),
                    '总市值': item.get('totalMv', 0),
                    '量比': item.get('volumeRatio', 1),
                    '换手率': item.get('turnoverRate', 0),
                    '股息率': item.get('dvTtm', 0),
                }
                _cache_set(_info_cache, code, info)
        return True
    except Exception as e:
        logger.debug(f'batch_get_stock_info: {e}')
        return False


def batch_get_capital_flow(codes, max_codes=20):
    """批量获取资金流向（wudao capital_flow，最多20只/次）"""
    try:
        from wudao_client import capital_flow as wudao_flow
        result = wudao_flow(codes[:max_codes])
        if result:
            sc = result.get('structuredContent') or {}
            items = sc.get('data', {}).get('items', [])
            for item in items:
                stock = item.get('stock', {})
                code = stock.get('code', '')
                if not code:
                    continue
                flows = item.get('flows', [])
                if flows:
                    latest = flows[-1]
                    info = _cache_get(_info_cache, code, 1) or {}
                    info.update({
                        '主力净流入': latest.get('mainForce', 0),
                        '超大单净流入': latest.get('superLarge', 0),
                        '大单净流入': latest.get('large', 0),
                    })
                    _cache_set(_info_cache, code, info)
        return True
    except Exception as e:
        logger.debug(f'batch_get_capital_flow: {e}')
        return False


def batch_get_auction_data(codes, trade_date=None):
    """批量获取集合竞价数据（wudao auction_data）"""
    from datetime import date
    d = trade_date or date.today().strftime('%Y%m%d')
    try:
        from wudao_client import auction_data as wudao_auction
        result = wudao_auction(codes, d)
        if result:
            sc = result.get('structuredContent') or {}
            rows = sc.get('data', {}).get('rows', [])
            result_dict = {}
            for item in rows:
                stock = item.get('stock', {})
                code = stock.get('code', '')
                if code:
                    result_dict[code] = {
                        'auction_chg': float(item.get('auctionChg', 0)),
                        'auction_amount': float(item.get('auctionAmount', 0)),
                        'auction_volume_ratio': float(item.get('auctionVolumeRatio', 0)),
                        'auction_turnover_rate': float(item.get('auctionTurnoverRate', 0)),
                    }
            return result_dict
    except Exception as e:
        logger.debug(f'batch_get_auction_data: {e}')
    return {}


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

    # 先用wudao批量获取（最多20只/次，省调用次数）
    wudao_warmed = 0
    try:
        from wudao_client import kline as wudao_kline
        batch_size = 20
        for i in range(0, len(uncached), batch_size):
            batch = uncached[i:i + batch_size]
            result = wudao_kline(batch, days)
            if result and result.get('content'):
                sc = result.get('structuredContent') or {}
                items = []
                if sc.get('data', {}).get('batch'):
                    items = sc['data'].get('items', [])
                if items:
                    for item in items:
                        stock_code = item.get('stock', {}).get('code', '')
                        if not stock_code:
                            continue
                        klines = item.get('rows', [])
                        if not klines or len(klines) < days * 0.5:
                            continue
                        rows = []
                        for k in klines:
                            rows.append({
                                'date': str(k.get('date', k.get('day', ''))).replace('-', ''),
                                'open': float(k.get('open', 0)),
                                'close': float(k.get('close', 0)),
                                'high': float(k.get('high', 0)),
                                'low': float(k.get('low', 0)),
                                'volume': float(k.get('volume', 0)),
                            })
                        if not rows:
                            continue
                        df = pd.DataFrame(rows).tail(days)
                        conn = sqlite3.connect(DB_PATH)
                        for _, row in df.iterrows():
                            try:
                                conn.execute(
                                    "INSERT OR REPLACE INTO daily_data VALUES (?,?,?,?,?,?,?)",
                                    (stock_code, str(row['date']), float(row['open']),
                                     float(row['close']), float(row['high']),
                                     float(row['low']), float(row['volume'])))
                            except: pass
                        conn.commit()
                        conn.close()
                        wudao_warmed += 1
            logger.info(f'batch_warm_cache(wudao): {wudao_warmed}/{len(uncached)}')
    except Exception as e:
        logger.debug(f'batch_warm_cache(wudao): {e}')

    # 剩余未缓存的用传统方式补齐
    remaining = [c for c in uncached if c not in
                 {r[0] for r in sqlite3.connect(DB_PATH).execute(
                     "SELECT code FROM daily_data GROUP BY code HAVING COUNT(*) >= ?", (days,)).fetchall()}]
    if remaining:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(get_stock_daily_cached, c, days): c for c in remaining}
            for fut in as_completed(futures):
                try:
                    df = fut.result()
                    if df is not None and len(df) >= days:
                        warmed += 1
                    else:
                        failed += 1
                except:
                    failed += 1

    return {'warmed': warmed + wudao_warmed, 'failed': failed, 'already_cached': already, 'total': total}
