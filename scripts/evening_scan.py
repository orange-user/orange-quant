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
    sector_map = {}  # code -> sector name
    if pool is not None and len(pool) > 500:
        print(f'  √ 获取到 {len(pool)} 只股票今日数据')
        # 建立代码→行业映射
        if '行业' in pool.columns:
            sector_map = dict(zip(pool['代码'].astype(str), pool['行业']))
        # 预筛选：涨跌幅>3%的才需要保证K线缓存
        hot_codes = pool[pool['涨跌幅'].abs() > 3]['代码'].tolist()
        print(f'  涨跌幅>3%: {len(hot_codes)}只，确保K线缓存...')
        n_fetched = 0
        for code in hot_codes[:20]:  # 最多20只（留余量给悟道日限50次）
            df = get_stock_daily_cached(code, days=60, force_refresh=True)
            if df is not None:
                n_fetched += 1
        print(f'  √ {n_fetched}只K线已缓存')
    else:
        print(f'  ! 快照获取失败或数据不足 ({len(pool) if pool is not None else 0})')

    # 第1.5步：获取板块热度排行（1次悟道调用）
    print('\n获取板块热度排行...')
    hot_sectors = []
    try:
        from _sector_heat import get_hot_concepts
        hot_sectors = get_hot_concepts(top_n=10)
        if hot_sectors:
            print(f'  √ 热门板块 TOP{len(hot_sectors)}:')
            for s in hot_sectors[:5]:
                print(f'    {s["name"]} {s["change_pct"]:+.2f}% (涨{s["up_count"]}跌{s["down_count"]})')
    except Exception as e:
        print(f'  ! 板块热度获取失败: {e}')

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
        sector = sector_map.get(code, '')
        # 检查是否在热门板块中
        in_hot = sector and any(s['name'] == sector for s in hot_sectors)
        entry = {
            'code': code,
            'score': score,
            'd1_open': detail['d1_open'],
            'd1_close': detail['d1_close'],
            'd1_chg': detail['d1_chg'],
            'd1_volume_ratio': detail['d1_volume_ratio'],
            'sector': sector,
            'sector_hot': in_hot,
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
        }
        watch_pool.append(entry)
        marker = '🔥' if score >= 70 else '⭐' if score >= 50 else '✓'
        sector_tag = f' [{sector}]' if sector else ''
        hot_tag = ' 🔥热门板块' if in_hot else ''
        print(f'  {marker} {code}{sector_tag}: 昨涨{detail["d1_chg"]}% 放量{detail["d1_volume_ratio"]}x '
              f'开{detail["d1_open"]}→收{detail["d1_close"]} 分{score}{hot_tag}')

    with open(WATCH_FILE, 'w') as f:
        json.dump(watch_pool, f, ensure_ascii=False, indent=2)
    print(f'\n观察池已保存 ({len(watch_pool)}只)')


if __name__ == '__main__':
    main()
