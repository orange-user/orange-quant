"""数据预热、交易日历、历史股票池重建"""
import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, ACCOUNT_CAPITAL
from data import batch_warm_cache

logger = logging.getLogger('backtest.data')


def build_trading_calendar(end_date=None, months_back=3):
    """从 SQLite stock_cache.db 提取历史交易日"""
    if end_date is None:
        end_date = datetime.now()
    cutoff = end_date - timedelta(days=months_back * 31 + 10)

    if not os.path.exists(DB_PATH):
        logger.error(f"SQLite数据库不存在: {DB_PATH}")
        return []

    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT DISTINCT date FROM daily_data ORDER BY date", conn)
        if df.empty:
            return []
        df['date'] = pd.to_datetime(df['date'])
        dates = sorted(df[df['date'] >= cutoff]['date'].unique())
        end_dt = pd.Timestamp(end_date)
        dates = [d for d in dates if d <= end_dt]
        logger.info(f"交易日历: {len(dates)}天 ({dates[0].date()} ~ {dates[-1].date()})")
        return dates
    except Exception as e:
        logger.error(f"读取交易日历失败: {e}")
        return []
    finally:
        conn.close()


# 总股本缓存（避免重复调用API）
_total_shares_cache = {}

def get_total_shares(code):
    """获取股票当前总股本（用于历史市值估算），带缓存"""
    if code in _total_shares_cache:
        return _total_shares_cache[code]

    # 从日线数据估算（避免调API）
    try:
        df = _read_sqlite_raw(code, 120)
        if df is not None and 'volume' in df.columns and len(df) >= 20:
            vol_med = df['volume'].median()
            if vol_med > 0:
                # 假设换手率中位数约2%来反推总股本
                estimated_shares = vol_med / 0.02
                if 1e8 < estimated_shares < 1e12:
                    _total_shares_cache[code] = estimated_shares
                    return estimated_shares
    except:
        pass

    # 通过API获取（akshare）
    try:
        import akshare as ak
        df = ak.stock_individual_info_em(symbol=code)
        for _, row in df.iterrows():
            if '总股本' in str(row['item']):
                val = float(row['value']) if row['value'] else None
                if val and 1e8 < val < 1e12:
                    _total_shares_cache[code] = val
                    return val
    except:
        pass

    _total_shares_cache[code] = None  # 标记为已查过
    return None


def _read_sqlite_raw(code, max_rows=200):
    """辅助: 直接从SQLite读取原始OHLC数据"""
    import sqlite3
    from config import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(
            "SELECT date, open, close, high, low, volume FROM daily_data "
            "WHERE code=? ORDER BY date DESC LIMIT ?",
            conn, params=(code, max_rows))
        conn.close()
        if df.empty:
            return None
        df = df.sort_values('date')
        df['date'] = pd.to_datetime(df['date'])
        return df
    except:
        return None


def warmup_cache(target_days=90, exclude_prefixes=('688', '689', '8', '4')):
    """预热数据：获取股票列表→批量缓存
    返回: dict {warmed, failed, already_cached, total}
    """
    from scraper import fetch_all_stocks

    all_stocks = fetch_all_stocks()
    if not all_stocks:
        logger.error("无法获取股票列表！")
        return {'warmed': 0, 'failed': 0, 'already_cached': 0, 'total': 0}

    codes = [s['code'] for s in all_stocks
             if not s['code'].startswith(exclude_prefixes)]
    logger.info(f"预热 {len(codes)} 只股票 {target_days} 天数据...")
    result = batch_warm_cache(codes, days=target_days, max_workers=48)
    logger.info(f"  已有: {result['already_cached']}, "
                f"新缓存: {result['warmed']}, 失败: {result['failed']}")
    return result


def get_historical_pool(as_of_date, exclude_prefixes=('688', '689', '8', '4'),
                        min_price=3.0, max_change_pct=9.5, min_market_cap=50e8,
                        min_cache_rows=60):
    """从SQLite重建某日的候选股票池
    只返回SQLite缓存充足（>=min_cache_rows行）的股票，避免触发外部API
    返回: list[dict] 每只股票包含 code/price/change_pct
    """
    as_of_str = str(pd.Timestamp(as_of_date).date())
    prefix_tuple = exclude_prefixes

    conn = sqlite3.connect(DB_PATH)
    try:
        # 放宽到30行（原60），早期数据也能参与回测
        well_cached = pd.read_sql_query(
            """SELECT code, COUNT(*) as cnt
               FROM daily_data
               GROUP BY code
               HAVING cnt >= ?""",
            conn, params=(min_cache_rows,))
    except Exception as e:
        logger.error(f"读取缓存统计失败: {e}")
        return []
    finally:
        conn.close()

    if well_cached.empty:
        logger.warning(f"无SQLite缓存充足的股票 (>{min_cache_rows}行)")
        return []

    # 过滤前缀
    well_cached = well_cached[
        ~well_cached['code'].str.startswith(prefix_tuple)
    ]
    cached_codes = set(well_cached['code'].tolist())
    logger.info(f"SQLite缓存充足: {len(cached_codes)}只股票")

    conn = sqlite3.connect(DB_PATH)
    try:
        as_of_dt = pd.Timestamp(as_of_date)
        prev_dt = as_of_dt - pd.Timedelta(days=30)

        df = pd.read_sql_query(
            """SELECT code, date, open, close, high, low, volume
               FROM daily_data
               WHERE date >= ? AND date <= ?
               ORDER BY code, date""",
            conn, params=(str(prev_dt.date()), as_of_str))
    except Exception as e:
        logger.error(f"读取历史数据失败: {e}")
        return []
    finally:
        conn.close()

    if df.empty:
        logger.warning(f"历史池 {as_of_str}: 数据库无数据")
        return []

    df['date'] = pd.to_datetime(df['date'])
    df = df[df['date'] <= pd.Timestamp(as_of_date)]

    pool = []
    shares_cache = {}
    skipped_data = 0
    skipped_price = 0
    skipped_change = 0
    skipped_mcap = 0

    for code, grp in df.groupby('code'):
        if code not in cached_codes:
            continue
        grp = grp.sort_values('date')
        if len(grp) < 2:
            skipped_data += 1
            continue

        last = grp.iloc[-1]
        prev = grp.iloc[-2]
        close = float(last['close'])
        prev_close = float(prev['close'])

        if close < min_price:
            skipped_price += 1
            continue

        change_pct = (close - prev_close) / prev_close * 100
        if abs(change_pct) > max_change_pct:
            skipped_change += 1
            continue

        # 市值过滤：min_market_cap<=0 表示跳过
        if min_market_cap > 0:
            total_shares = shares_cache.get(code)
            if total_shares is None:
                total_shares = get_total_shares(code)
                if total_shares:
                    shares_cache[code] = total_shares
            if total_shares and total_shares * close < min_market_cap:
                skipped_mcap += 1
                continue

        pool.append({
            'code': code,
            'price': close,
            'change_pct': round(change_pct, 2),
        })

    logger.info(f"历史池 {as_of_str}: {len(pool)}只候选 "
                f"(跳过: 数据不足{skipped_data} 价格{skipped_price} "
                f"涨跌幅{skipped_change} 市值{skipped_mcap})")
    return pool
