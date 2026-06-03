"""收盘后扫描（15:00 cron）：检测D1放量大阳→生成明日观察池"""
import sys, os, json, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from _pattern_recognition import scan_d1_candidates

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
WATCH_FILE = os.path.join(DATA_DIR, 'd1_watch_pool.json')


def main():
    print(f'=== 收盘扫描 {datetime.now().strftime("%Y-%m-%d %H:%M")} ===\n')

    # 第1步：调用悟道获取今日全市场快照（1次调用）
    print('获取今日全市场快照...')
    from data import _get_pool_snapshot, get_stock_daily_cached
    pool = _get_pool_snapshot()
    if pool is not None and len(pool) > 500:
        print(f'  √ 获取到 {len(pool)} 只股票今日数据')
        # 预筛选：涨跌幅>3%的才需要保证K线缓存
        hot_codes = pool[pool['涨跌幅'].abs() > 3]['代码'].tolist()
        print(f'  涨跌幅>3%: {len(hot_codes)}只，确保K线缓存...')
        n_fetched = 0
        for code in hot_codes[:50]:  # 最多50只（留余量给悟道日限）
            df = get_stock_daily_cached(code, days=60)
            if df is not None:
                n_fetched += 1
        print(f'  √ {n_fetched}只K线已缓存')
    else:
        print(f'  ! 快照获取失败或数据不足 ({len(pool) if pool is not None else 0})')

    # 第2步：扫描D1放量大阳（读SQLite缓存）
    print('\n扫描D1放量大阳模式...')
    results = scan_d1_candidates(max_stocks=500)
    if not results:
        print('今日无D1放量大阳候选')
        with open(WATCH_FILE, 'w') as f:
            json.dump([], f)
        return

    # 取TOP20写入观察池
    top = results[:20]
    watch_pool = []
    print(f'\n明日观察池 ({len(top)}只):')
    for code, score, pname, detail in top:
        entry = {
            'code': code,
            'score': score,
            'd1_open': detail['d1_open'],
            'd1_close': detail['d1_close'],
            'd1_chg': detail['d1_chg'],
            'd1_volume_ratio': detail['d1_volume_ratio'],
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
        }
        watch_pool.append(entry)
        marker = '🔥' if score >= 70 else '⭐' if score >= 50 else '✓'
        print(f'  {marker} {code}: 昨涨{detail["d1_chg"]}% 放量{detail["d1_volume_ratio"]}x '
              f'开{detail["d1_open"]}→收{detail["d1_close"]} 分{score}')

    with open(WATCH_FILE, 'w') as f:
        json.dump(watch_pool, f, ensure_ascii=False, indent=2)
    print(f'\n观察池已保存 ({len(watch_pool)}只)')


if __name__ == '__main__':
    main()
