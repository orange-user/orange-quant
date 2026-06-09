"""Monkey-patch引擎适配器：让评分引擎在历史日期上下文中运行

设计：一次性启用补丁，持续使用，回测结束后恢复。
避免并发 unpatch 导致的竞态条件。
"""
import threading
import logging
import pandas as pd
import sqlite3
import numpy as np

from config import DB_PATH

import data as data_module
import engine as engine_module

logger = logging.getLogger('backtest.adapter')

_context = threading.local()

# ===== 原始函数引用（用于恢复） =====
_orig_functions = {}


def _read_sqlite_cache(code, max_rows=200):
    """直接读SQLite缓存，不触发网络请求"""
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
    except Exception as e:
        logger.debug(f"SQLite直读失败 {code}: {e}")
        return None


# 回测中SQLite读取缓存（避免每策略重复读）
_backtest_data_cache = {}  # (code, as_of_date_str) -> DataFrame

def _patched_get_daily(code, days=60):
    """日期感知版：直接读SQLite，截断到回测日期，重算滚动指标"""
    as_of = getattr(_context, 'as_of_date', None)
    if as_of is None:
        return _read_sqlite_cache(code, max_rows=200)

    cache_key = (code, str(as_of.date()))
    if cache_key in _backtest_data_cache:
        cached = _backtest_data_cache[cache_key]
        return cached if cached is not None else None
    else:
        df = _read_sqlite_cache(code, max_rows=200)
        if df is None:
            return None
        df = df[df['date'] <= as_of].copy()
        if len(df) < min(days, 5):
            _backtest_data_cache[cache_key] = None
            return None
        # 重算衍生字段
        df['returns'] = df['close'].pct_change()
        df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()

        cols = ['date', 'open', 'close', 'high', 'low', 'volume',
                'returns', 'volume_ratio', 'ma5', 'ma20']
        available = [c for c in cols if c in df.columns]
        result = df[available].dropna()
        if len(result) >= min(days, 10):
            _backtest_data_cache[cache_key] = result.tail(min(len(result), days + 20))
        else:
            _backtest_data_cache[cache_key] = df.tail(min(len(df), days))

    return _backtest_data_cache.get(cache_key)


_INDEX_CACHE = None

def _load_index_cache():
    """加载本地缓存的指数数据"""
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE
    import os, json
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'data', 'index_cache.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
        df = pd.DataFrame(d)
        df['date'] = pd.to_datetime(df['date'])
        _INDEX_CACHE = df
        logger.info(f"指数缓存加载: {len(df)}天")
    except:
        _INDEX_CACHE = pd.DataFrame()
    return _INDEX_CACHE


def _patched_get_index(code="sh000300", days=60):
    """日期感知版：从本地缓存读取指数数据"""
    idx_df = _load_index_cache()
    as_of = getattr(_context, 'as_of_date', None)
    if idx_df.empty:
        return None

    if as_of is not None:
        idx_df = idx_df[idx_df['date'] <= as_of].tail(days).copy()
    else:
        idx_df = idx_df.tail(days).copy()

    if len(idx_df) < 20:
        return None
    if 'returns' not in idx_df.columns or idx_df['returns'].isna().all():
        idx_df['returns'] = idx_df['close'].pct_change()
    return idx_df.dropna()


def _patched_get_news(*args, **kwargs):
    """回测中禁用新闻因子"""
    return None


def _patched_mcp_search(*args, **kwargs):
    """回测中禁用 Ruflo 相似度搜索"""
    return []


# 基本面信息本地缓存（避免回测中调akshare）
_INFO_CACHE_PATH = None

def _get_info_cache():
    global _INFO_CACHE_PATH
    if _INFO_CACHE_PATH is None:
        import os
        _INFO_CACHE_PATH = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'stock_info_cache.json')
    try:
        with open(_INFO_CACHE_PATH, 'r', encoding='utf-8') as f:
            return json_module.load(f)
    except:
        return {}


def _patched_get_stock_info(code):
    """基本面——用本地JSON缓存，避免调akshare"""
    cache = _get_info_cache()
    return cache.get(code, {})


def _patched_get_sector(code):
    """回测中禁用板块查询（依赖akshare，网络不稳定）"""
    return []


def _patched_detect_indicator_cycle(*args, **kwargs):
    """回测中禁用因子周期检测（内部调 _get_pool_snapshot 触发akshare）"""
    return {"status": "unknown", "ic_mean": 0, "recommendation": ""}


def enable_backtest_mode():
    """启用回测模式：替换data模块中的函数为日期感知版
    一次性调用，回测结束时 disable_backtest_mode 恢复
    """
    # 保存原函数
    if not _orig_functions:
        _orig_functions['get_stock_daily_cached'] = data_module.get_stock_daily_cached
        _orig_functions['get_index_daily'] = data_module.get_index_daily
        _orig_functions['get_global_news_cached'] = data_module.get_global_news_cached
        _orig_functions['get_stock_info'] = data_module.get_stock_info
        _orig_functions['get_stock_sector'] = data_module.get_stock_sector
        _orig_functions['detect_indicator_cycle'] = getattr(engine_module, 'detect_indicator_cycle', None)
        _orig_functions['engine_news'] = getattr(engine_module, 'get_global_news_cached', None)

    # 替换为补丁版（同时覆盖 data_module 和 engine_module）
    # engine_module 用 from data import * 有自己的引用，两个都要改
    for mod in (data_module, engine_module):
        mod.get_stock_daily_cached = _patched_get_daily
        mod.get_index_daily = _patched_get_index
        mod.get_global_news_cached = _patched_get_news
        mod.get_stock_info = _patched_get_stock_info
        mod.get_stock_sector = _patched_get_sector
    engine_module.detect_indicator_cycle = _patched_detect_indicator_cycle

    # 禁用Ruflo
    try:
        import mcp_client
        if 'safe_search' not in _orig_functions:
            _orig_functions['safe_search'] = getattr(engine_module, 'safe_search', None)
        engine_module.safe_search = _patched_mcp_search
    except ImportError:
        pass

    # 禁用协整策略（内部调 _get_pool_snapshot + tqdm，回测中没必要）
    if 'strategy_cointegration' not in _orig_functions:
        _orig_functions['strategy_cointegration'] = getattr(engine_module, 'strategy_cointegration', None)
    engine_module.strategy_cointegration = lambda code: 0

    logger.info("回测模式已启用")


def disable_backtest_mode():
    """恢复原始函数"""
    if not _orig_functions:
        return

    for mod in (data_module, engine_module):
        mod.get_stock_daily_cached = _orig_functions.get(
            'get_stock_daily_cached', mod.get_stock_daily_cached)
        mod.get_index_daily = _orig_functions.get(
            'get_index_daily', mod.get_index_daily)
        mod.get_global_news_cached = _orig_functions.get(
            'get_global_news_cached', mod.get_global_news_cached)
        mod.get_stock_info = _orig_functions.get(
            'get_stock_info', mod.get_stock_info)
        mod.get_stock_sector = _orig_functions.get(
            'get_stock_sector', mod.get_stock_sector)
    engine_module.detect_indicator_cycle = _orig_functions.get(
        'detect_indicator_cycle', engine_module.detect_indicator_cycle)

    try:
        import mcp_client
        if _orig_functions.get('safe_search'):
            engine_module.safe_search = _orig_functions['safe_search']
    except ImportError:
        pass

    # 恢复协整策略
    if _orig_functions.get('strategy_cointegration'):
        engine_module.strategy_cointegration = _orig_functions['strategy_cointegration']

    _context.as_of_date = None
    logger.info("回测模式已禁用")


def set_backtest_date(as_of_date):
    """设置当前线程的回测日期（线程安全：threading.local）"""
    _context.as_of_date = pd.Timestamp(as_of_date) if not isinstance(
        as_of_date, pd.Timestamp) else as_of_date
    # 日期变化时清空数据缓存，避免跨日混淆
    _backtest_data_cache.clear()


def clear_backtest_date():
    """清除当前线程的回测日期"""
    _context.as_of_date = None


def score_stock_at_date(code, as_of_date):
    """在指定历史日期对股票评分
    必须在 enable_backtest_mode 已调用的前提下执行
    线程安全：每线程通过 _context.as_of_date 设置自己的日期
    """
    set_backtest_date(as_of_date)
    try:
        result = engine_module.calculate_comprehensive_score(code)
        if result and result.get('signal', 30) > 25:
            # 补上实际收盘价和涨跌幅（用更多行避免时间截断问题）
            df = _read_sqlite_cache(code, max_rows=200)
            if df is not None:
                hist = df[df['date'] <= pd.Timestamp(as_of_date)]
                if len(hist) >= 2:
                    last = hist.iloc[-1]
                    prev = hist.iloc[-2]
                    result['price'] = float(last['close'])
                    result['change_pct'] = round(
                        (float(last['close']) - float(prev['close']))
                        / float(prev['close']) * 100, 2)
                elif len(hist) >= 1:
                    result['price'] = float(hist.iloc[-1]['close'])
        return result
    except Exception as e:
        logger.debug(f"评分失败 {code} @ {as_of_date}: {e}")
        return None
    finally:
        clear_backtest_date()
