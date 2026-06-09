"""SQLite缓存预热：用腾讯K线拉全市场日线数据到SQLite（无限流，不依赖akshare）"""
import sys, os, json, warnings, time, random
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3

from config import DB_PATH

# 腾讯K线接口无限制，但保持礼貌
DELAY_BASE = 0.2
DELAY_JITTER = 0.1
MAX_WORKERS = 8       # 可以更高，腾讯不限制
RETRY_LIMIT = 2

# 拉取天数（约2年）
FETCH_DAYS = 500


def get_all_codes():
    """获取全市场股票列表（优先SQLite缓存，回退scraper/akshare）"""
    # SQLite已有3274只，直接用
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT DISTINCT code FROM daily_data").fetchall()
        conn.close()
        if len(rows) > 2000:
            codes = [r[0] for r in rows]
            codes = [c for c in codes if c.startswith(('0','3','6'))
                     and not c.startswith(('300','301','688','689','8','4'))]
            return sorted(codes)
    except:
        pass
    # 回退scraper
    try:
        from scraper import fetch_all_stocks
        stocks = fetch_all_stocks()
        if stocks and len(stocks) > 2000:
            codes = [s['code'] for s in stocks]
            codes = [c for c in codes if not c.startswith(('4','8')) and len(c) == 6]
            return sorted(codes)
    except:
        pass
    # 回退akshare
    import akshare as ak
    codes = set()
    try:
        df = ak.stock_zh_a_spot_em()
        if len(df) > 1000:
            codes.update(df['代码'].astype(str).str.zfill(6).tolist())
    except:
        pass
    return sorted([c for c in codes if c.startswith(('0','3','6'))
                   and not c.startswith(('300','301','688','689','8','4'))])


def stock_has_data(code):
    """检查SQLite缓存是否已有足够数据"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT COUNT(*) FROM daily_data WHERE code=?", (code,)
    ).fetchone()
    conn.close()
    return row and row[0] >= FETCH_DAYS * 0.6


def fetch_and_cache(code):
    """从腾讯K线拉取数据并写入SQLite"""
    for attempt in range(RETRY_LIMIT + 1):
        try:
            from scraper import _tencent_kline
            df = _tencent_kline(code, days=FETCH_DAYS)
            if df is None or len(df) < 60:
                time.sleep(DELAY_BASE + random.random() * DELAY_JITTER)
                return {'code': code, 'rows': 0, 'status': 'no_data'}

            df['code'] = code
            # 确保date是YYYYMMDD格式字符串
            df['date'] = df['date'].astype(str).str.replace('-', '')

            # 写入SQLite（去重）
            conn = sqlite3.connect(DB_PATH)
            existing = set(
                row[0] for row in conn.execute(
                    "SELECT date FROM daily_data WHERE code=?", (code,)
                ).fetchall()
            )
            new_rows = df[~df['date'].isin(existing)]
            if not new_rows.empty:
                new_rows[['code', 'date', 'open', 'close', 'high', 'low', 'volume']].to_sql(
                    'daily_data', conn, if_exists='append', index=False
                )
            conn.close()

            time.sleep(DELAY_BASE + random.random() * DELAY_JITTER)
            return {'code': code, 'rows': len(df), 'new': len(new_rows), 'status': 'ok'}

        except Exception as e:
            if attempt < RETRY_LIMIT:
                time.sleep(DELAY_BASE * 2)
                continue
            return {'code': code, 'rows': 0, 'status': f'err:{str(e)[:50]}'}
    return {'code': code, 'rows': 0, 'status': 'max_retry'}


if __name__ == '__main__':
    print(f'SQLite缓存预热 {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'数据源: 腾讯K线(ifzq.gtimg.cn) | 并发: {MAX_WORKERS}')
    print(f'延时: {DELAY_BASE}s+{DELAY_JITTER}s | 重试: {RETRY_LIMIT}次')
    print(f'每只拉取: {FETCH_DAYS}条日线\n')

    codes = get_all_codes()
    todo = [c for c in codes if not stock_has_data(c)]
    skip = len(codes) - len(todo)
    print(f'全市场: {len(codes)}只 | 已有缓存: {skip}只 | 需拉取: {len(todo)}只\n')

    if not todo:
        print('所有股票已缓存，无需预热')
        exit()

    t0 = time.time()
    done = ok = fail = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_and_cache, c): c for c in todo}
        for f in as_completed(futures):
            done += 1
            r = f.result()
            if r['status'] == 'ok':
                ok += 1
            else:
                fail += 1

            if done % 100 == 0 or done == len(todo):
                elapsed = time.time() - t0
                rate = done / max(elapsed, 1) * 60
                eta = (len(todo) - done) / max(rate / 60, 0.01)
                print(f'  [{done}/{len(todo)}] [OK]{ok} [FAIL]{fail} '
                      f'速率{rate:.0f}只/分 预计剩余{eta:.0f}分')

    elapsed = time.time() - t0
    print(f'\n完成! {done}只, [OK]{ok} [FAIL]{fail}, 耗时{elapsed:.0f}s({elapsed/60:.1f}分)')

    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_data").fetchone()[0]
    conn.close()
    print(f'SQLite缓存总计: {total}只股票')
