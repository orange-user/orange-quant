"""回测主循环——逐日模拟买卖"""
import os
import sys
import logging
from datetime import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np
import pandas as _pd
_EMPTY_DF = _pd.DataFrame()

# 禁用akshare网络调用（源级别替换，所有模块生效）
import akshare as _ak
_ak.stock_individual_info_em = lambda *a,**kw: _EMPTY_DF
_ak.stock_board_concept_cons_em = lambda *a,**kw: _EMPTY_DF
_ak.stock_info_sh_name_code = lambda *a,**kw: _EMPTY_DF
_ak.stock_info_sz_name_code = lambda *a,**kw: _EMPTY_DF
_ak.stock_info_bj_name_code = lambda *a,**kw: _EMPTY_DF
_ak.stock_zh_a_spot_em = lambda *a,**kw: _EMPTY_DF
_ak.stock_zh_a_spot = lambda *a,**kw: _EMPTY_DF      # ❗之前漏了Sina版
_ak.stock_info_global_em = lambda *a,**kw: _EMPTY_DF
_ak.stock_zh_a_hist = lambda *a,**kw: _EMPTY_DF
_ak.stock_zh_a_hist_tx = lambda *a,**kw: _EMPTY_DF   # ❗之前漏了腾讯版
_ak.stock_zh_index_daily = lambda *a,**kw: _EMPTY_DF
_ak.stock_individual_fund_flow = lambda *a,**kw: _EMPTY_DF

# 禁用scraper（也会触发网络请求）
import scraper as _scraper
if hasattr(_scraper, 'fetch_all_stocks'):
    _scraper.fetch_all_stocks = lambda **kw: _EMPTY_DF.to_dict() if hasattr(_EMPTY_DF, 'to_dict') else []

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 在导入engine前禁用mcp_client（避免尝试连接本地代理）
import mcp_client as _mcp_client
_mcp_client.safe_search = lambda *a,**kw: []

from engine import quick_prescreen
import data as data_module

from backtest.data_provider import (
    build_trading_calendar, get_historical_pool, warmup_cache
)
from backtest.engine_adapter import (
    enable_backtest_mode, disable_backtest_mode,
    score_stock_at_date, set_backtest_date, clear_backtest_date
)
from backtest.position_manager import BacktestPortfolio, calc_backtest_kelly
from backtest.sell_simulator import evaluate_sell_conditions

logger = logging.getLogger('backtest.runner')

# 保存原函数引用（卖出逻辑需要用原始数据）
_orig_get_daily = data_module.get_stock_daily_cached


def _get_ohlc_for_sell(code, as_of_date):
    """卖出逻辑用：直接读SQLite获取某日OHLC数据（不经过patch）"""
    import sqlite3
    from config import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(
            "SELECT date, open, close, high, low, volume FROM daily_data "
            "WHERE code=? AND date <= ? ORDER BY date DESC LIMIT 5",
            conn, params=(code, str(pd.Timestamp(as_of_date).date())))
        conn.close()
        if df.empty:
            return None
        df = df.sort_values('date')
        df['date'] = pd.to_datetime(df['date'])
        return df if len(df) >= 1 else None
    except:
        return None


def run_backtest(start_date=None, end_date=None, initial_capital=100000,
                 top_n=3, strong_signal_threshold=85, slippage=0.001, max_workers=16):
    """运行完整回测
    slippage: 买卖滑点比例，默认0.001（10bps）
    """
    # ---------- 1. 确定日期 ----------
    if end_date is None:
        end_date = datetime.now()
    else:
        end_date = pd.Timestamp(end_date)
    if start_date is None:
        start_date = end_date - pd.Timedelta(days=90)
        # 动态修正：避免起始日数据不足
        import sqlite3
        from config import DB_PATH
        try:
            conn = sqlite3.connect(DB_PATH)
            min_date = pd.read_sql_query("SELECT MIN(date) FROM daily_data", conn).iloc[0, 0]
            conn.close()
            if min_date:
                min_start = pd.Timestamp(min_date) + pd.Timedelta(days=30)
                if start_date < min_start:
                    start_date = min_start
                    logger.info(f"数据库最早{min_date}，起始日自动调整为{start_date.date()}")
        except:
            pass
    else:
        start_date = pd.Timestamp(start_date)

    # ---------- 2. 获取交易日历 ----------
    trading_days = build_trading_calendar(end_date, months_back=3)
    trading_days = [d for d in trading_days if start_date <= d <= end_date]
    if len(trading_days) < 5:
        logger.error(f"交易日不足: {len(trading_days)}天")
        return None

    total_days = len(trading_days)
    logger.info(f"回测期间: {trading_days[0].date()} ~ {trading_days[-1].date()}")
    logger.info(f"交易日: {total_days}天 | 初始资金: {initial_capital:.0f}")
    logger.info(f"每日买入: TOP{top_n} | 强势信号阈值: {strong_signal_threshold}")

    # ---------- 3. 初始化 ----------
    portfolio = BacktestPortfolio(initial_capital)

    # 启用回测模式（全局替换data模块函数，线程安全）
    enable_backtest_mode()

    try:
        # ---------- 4. 逐日回测 ----------
        for idx, today in enumerate(trading_days):
            # 进度条
            pct = (idx + 1) / total_days * 100
            bar_len = 30
            filled = int(bar_len * (idx + 1) // total_days)
            bar = '#' * filled + '-' * (bar_len - filled)
            sys.stdout.write(f"\r回测进度: [{bar}] {idx+1}/{total_days}天 ({pct:.0f}%) 交易{len(portfolio.trade_history)}笔 持仓{len(portfolio.positions)}只")
            sys.stdout.flush()
            today_str = today.strftime('%Y-%m-%d')
            today_ts = today

            # ===== 早盘：处理卖出 =====
            current_prices = {}
            for pos in list(portfolio.positions):
                code = pos['code']
                days_held = idx - pos.get('buy_day_idx', idx)

                hist = _get_ohlc_for_sell(code, today_ts)
                if hist is None or len(hist) < 1:
                    continue

                row = hist.iloc[-1]
                current_prices[code] = float(row['close'])
                prev_close = row['close']
                if len(hist) >= 2:
                    prev_close = hist.iloc[-2]['close']

                result = evaluate_sell_conditions(
                    position=pos,
                    open_price=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    prev_close=float(prev_close),
                    days_since_buy=days_held,
                    slippage=slippage,
                )

                if result.should_sell:
                    trade = portfolio.close_position(
                        code, result.sell_price, today_str, result.reason, slippage=slippage,
                    )
                    if trade:
                        trade['hold_days'] = days_held

            # ===== 尾盘：扫描买入 (TOP1) =====
            pool = get_historical_pool(today_ts, min_market_cap=0)
            if not pool or len(pool) < 10:
                logger.debug(f"  {today_str}: 候选池不足({len(pool)})")
                portfolio.snapshot(today_str, current_prices)
                continue

            # 精简：取涨幅绝对值前500
            pool.sort(key=lambda x: abs(x['change_pct']), reverse=True)
            pool = pool[:500]
            codes = [s['code'] for s in pool]

            # --- 预筛选（并行，每个线程自行设置日期） ---
            prescores = []
            with ThreadPoolExecutor(max_workers=16) as ex:
                def prescreen_task(code):
                    set_backtest_date(today_ts)
                    try:
                        ps = quick_prescreen(code)
                        return (code, ps) if ps and ps.get('prescore', 0) > 0 else None
                    finally:
                        clear_backtest_date()

                fut_map = {ex.submit(prescreen_task, code): code for code in codes}
                for fut in as_completed(fut_map):
                    try:
                        r = fut.result()
                        if r:
                            prescores.append(r[1])
                    except Exception:
                        continue

            if not prescores:
                portfolio.snapshot(today_str, current_prices)
                continue

            top40 = sorted(prescores, key=lambda x: x['prescore'], reverse=True)[:40]
            top40_codes = [p['code'] for p in top40]

            # --- 全量评分（并行，线程安全） ---
            scored = []
            with ThreadPoolExecutor(max_workers=16) as ex:
                fut_map = {ex.submit(score_stock_at_date, code, today_ts): code
                           for code in top40_codes}
                for fut in as_completed(fut_map):
                    try:
                        result = fut.result()
                        if result and result.get('signal', 0) > 25:  # 放宽到25（原30）
                            scored.append(result)
                    except Exception:
                        continue

            if not scored:
                portfolio.snapshot(today_str, current_prices)
                continue

            # 取TOP N → 买入
            scored.sort(key=lambda x: x['signal'], reverse=True)
            for r in scored[:top_n]:
                code = r['code']
                buy_price = r.get('price', 0)
                signal = r.get('signal', 0)
                if buy_price <= 0 or signal <= 0:
                    continue
                if any(p['code'] == code for p in portfolio.positions):
                    continue

                min_cost = 100 * buy_price * (1 + 0.00025) + 5
                if min_cost > portfolio.cash:
                    continue

                kelly = calc_backtest_kelly(
                    buy_price, signal, portfolio.trade_history, portfolio.cash
                )
                shares_hands = kelly['suggested_shares']
                if shares_hands < 1:
                    if signal >= 60:
                        shares_hands = 1
                    else:
                        continue

                cost_est = shares_hands * 100 * buy_price * (1 + 0.00025) + 5
                if cost_est > portfolio.cash:
                    shares_hands = max(1, int((portfolio.cash - 5) /
                                              (100 * buy_price * (1 + 0.00025))))
                    if shares_hands < 1:
                        continue

                portfolio.open_position(
                    code=code, buy_price=buy_price, shares_hands=shares_hands,
                    signal=signal, buy_date=today_str, buy_day_idx=idx,
                    slippage=slippage,
                )

            # --- 记录每日快照 ---
            portfolio.snapshot(today_str, current_prices)

            if (idx + 1) % 5 == 0 or idx == len(trading_days) - 1:
                logger.info(f"[{idx+1}/{len(trading_days)}] "
                            f"cash={portfolio.cash:.0f} "
                            f"pos={len(portfolio.positions)} "
                            f"trades={len(portfolio.trade_history)}")

        # ---------- 5. 强制平仓 ----------
        for pos in list(portfolio.positions):
            code = pos['code']
            last_day = trading_days[-1]
            hist = _get_ohlc_for_sell(code, last_day)
            if hist is not None and len(hist) > 0:
                close_price = hist.iloc[-1]['close']
                trade = portfolio.close_position(
                    code, close_price,
                    last_day.strftime('%Y-%m-%d'), '强制平仓(回测结束)',
                    slippage=slippage,
                )
                if trade:
                    trade['hold_days'] = len(trading_days) - pos['buy_day_idx']

        portfolio.snapshot(trading_days[-1].strftime('%Y-%m-%d'), current_prices)

    finally:
        disable_backtest_mode()

    # 补充hold_days
    for t in portfolio.trade_history:
        if t.get('hold_days') is None:
            try:
                bd = pd.Timestamp(t['buy_date'])
                sd = pd.Timestamp(t['sell_date'])
                t['hold_days'] = (sd - bd).days
            except:
                t['hold_days'] = 1

    return {
        'trades': portfolio.trade_history,
        'daily_snapshots': portfolio.daily_values,
        'initial_capital': initial_capital,
        'final_capital': round(portfolio.total_value(), 2),
        'config': {
            'start_date': str(trading_days[0].date()),
            'end_date': str(trading_days[-1].date()),
            'trading_days': len(trading_days),
            'initial_capital': initial_capital,
            'top_n': top_n,
            'strong_signal_threshold': strong_signal_threshold,
        }
    }
