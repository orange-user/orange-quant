"""Pulse Orange v4 策略层 (Layer 1)
8个纯OHLCV+成交量结构策略
零指标(无MA/RSI/MACD/KDJ/布林)
"""
import numpy as np
import pandas as pd
from v4_base import Strategy


# ====================================================================
# S1: 尾盘内在动量 (EOD Inner Momentum)
# ====================================================================
class S1_EODMomentum(Strategy):
    """问题：尾盘成交量暴露了哪些主力的真实意图？
    T+1制度下，尾盘买入必须承受隔夜风险，因此尾盘行为比早盘"真"得多。
    """
    name = 's1_eod_momentum'
    description = '尾盘内在动量 - T+1锁定下的尾盘真实意图'
    max_hold_days = 2
    stop_loss_pct = 0.03

    def entry_signals(self, df, date_idx):
        """尾盘判定条件(基于日线):
        1. 最后30分钟量占比 > 25% (需要分钟线)
        2. 收盘在最高5%区间
        3. 前3根K线阳线>=3根, 高点递增
        4. 未出现尾盘最后5分钟回落>3%
        """
        if date_idx < 5:
            return False

        # 条件2: 收盘位置
        row = df.iloc[date_idx]
        close = float(row['close'])
        high = float(row['high'])
        low = float(row['low'])
        open_p = float(row['open'])

        rng = high - low
        if rng <= 0:
            return False
        close_position = (close - low) / rng

        # 收盘在最高5%区间内
        if close_position < 0.95:
            return False

        # 日内涨幅不能太大(排除已经涨多的)
        intraday_pct = (close / open_p - 1) * 100
        if intraday_pct > 8:
            return False

        # 条件3: 前3根K线 (日线级别)
        yang_count = 0
        high_rising = True
        prev_high = None
        for i in range(date_idx - 1, date_idx - 4, -1):
            if i < 0:
                break
            r = df.iloc[i]
            r_close = float(r['close'])
            r_open = float(r['open'])
            if r_close > r_open:
                yang_count += 1
            r_high = float(r['high'])
            if prev_high is not None and r_high > prev_high:
                high_rising = False
            prev_high = r_high

        if yang_count < 2:
            return False

        # 成交量: 当根量大于前5日均量
        volumes = df['volume'].values.astype(float)
        if date_idx >= 5:
            prev_vol_mean = np.mean(volumes[date_idx-5:date_idx])
            if prev_vol_mean > 0 and volumes[date_idx] < prev_vol_mean:
                return False

        return True

    def exit_signals(self, df, entry_idx, current_idx):
        days_held = current_idx - entry_idx
        row = df.iloc[current_idx]

        # 强制2日卖出
        if days_held >= 2:
            return True, 'time_exit'

        pnl = (float(row['close']) / float(df.iloc[entry_idx]['open']) - 1) * 100
        if pnl < -3:
            return True, 'stop_loss'
        if pnl > 5:
            return True, 'take_profit'

        return False, ''


# ====================================================================
# S2: 放量关键位置确认 (Volume Key Level Confirmation)
# ====================================================================
class S2_VolumeBreakout(Strategy):
    """问题：突破关键位时成交量告诉我是真是假？
    倍量+位置突破+阳线收高 = 真实突破
    """
    name = 's2_volume_breakout'
    description = '放量关键位置突破确认'
    max_hold_days = 5
    stop_loss_pct = 0.04

    def entry_signals(self, df, date_idx):
        if date_idx < 25:
            return False

        row = df.iloc[date_idx]
        close = float(row['close'])
        high = float(row['high'])
        low = float(row['low'])
        open_p = float(row['open'])
        volume = float(row['volume'])

        # 1. 价格突破前20日最高点的98%
        highs_20 = df['high'].values.astype(float)
        max_high_20 = np.max(highs_20[date_idx-20:date_idx]) if date_idx >= 20 else 0
        if high < max_high_20 * 0.98:
            return False

        # 2. 成交量 > 前20日中位数的1.8倍
        vols_20 = df['volume'].values.astype(float)
        prev_vols = vols_20[date_idx-20:date_idx] if date_idx >= 20 else vols_20[:date_idx]
        vol_median = np.median(prev_vols) if len(prev_vols) > 0 else 1
        if vol_median <= 0 or volume < vol_median * 1.8:
            return False

        # 3. 阳线且收盘在振幅上70%区间
        rng = high - low
        if rng <= 0:
            return False
        close_position = (close - low) / rng
        if not (close > open_p and close_position > 0.70):
            return False

        # 4. 成交量加速: 后5根累积量 > 前5根累积量
        if date_idx >= 10:
            vol_prev5 = np.sum(vols_20[date_idx-10:date_idx-5]) if date_idx >= 10 else 0
            vol_prev5_recent = np.sum(vols_20[date_idx-5:date_idx]) if date_idx >= 5 else 0
            if vol_prev5 > 0 and vol_prev5_recent < vol_prev5:
                return False

        # 5. 涨幅不要太大(排除连续涨停)
        daily_pct = (close / float(df.iloc[date_idx-1]['close']) - 1) * 100
        if daily_pct > 9:
            return False

        return True

    def exit_signals(self, df, entry_idx, current_idx):
        days_held = current_idx - entry_idx
        entry_price = float(df.iloc[entry_idx]['open'])
        cur_close = float(df.iloc[current_idx]['close'])
        pnl = (cur_close / entry_price - 1) * 100

        # 止损
        if pnl < -4:
            return True, 'stop_loss'
        # 止盈
        if pnl > 8:
            return True, 'take_profit'
        # 跌破入场点 (保护)
        if days_held >= 2 and pnl < 0:
            return True, 'break_even_exit'
        # 时间止损
        if days_held >= 5:
            return True, 'time_exit'
        return False, ''


# ====================================================================
# S3: 区间极限量能反转 (Range Extreme Volume Reversal)
# ====================================================================
class S3_RangeReversal(Strategy):
    """问题：到区间边界时量能指示突破还是反转？
    大成交在边界位置却无法推远 → 抛压大，该反转了
    """
    name = 's3_range_reversal'
    description = '区间极限量能反转 - 震荡市中在边界放量却无法突破'
    max_hold_days = 5
    stop_loss_pct = 0.03

    def entry_signals(self, df, date_idx):
        if date_idx < 25:
            return False

        row = df.iloc[date_idx]
        close = float(row['close'])
        low = float(row['low'])
        high = float(row['high'])
        open_p = float(row['open'])
        volume = float(row['volume'])

        # 20日区间
        highs_20 = df['high'].values.astype(float)
        lows_20 = df['low'].values.astype(float)
        max_high = np.max(highs_20[date_idx-20:date_idx]) if date_idx >= 20 else 0
        min_low = np.min(lows_20[date_idx-20:date_idx]) if date_idx >= 20 else 0

        if max_high <= min_low:
            return False

        # 检查是否在区间下边界 (最低点+2%以内)
        at_lower_bound = close <= min_low * 1.02
        if not at_lower_bound:
            return False

        rng = high - low
        if rng <= 0:
            return False

        # 下影线 >= 振幅40%
        lower_shadow = min(open_p, close) - low
        shadow_ratio = lower_shadow / rng

        # 放量 + 下影线拒绝 + 收盘在振幅上半区
        vols_20 = df['volume'].values.astype(float)
        prev_vols = vols_20[date_idx-20:date_idx] if date_idx >= 20 else vols_20[:date_idx]
        vol_median = np.median(prev_vols) if len(prev_vols) > 0 else 1

        if volume > vol_median * 1.5 and shadow_ratio >= 0.35 and close > open_p:
            return True

        # 缩量接近低点也考虑(卖盘枯竭)
        if volume < vol_median * 0.6 and close > open_p and shadow_ratio > 0.3:
            return True

        return False

    def exit_signals(self, df, entry_idx, current_idx):
        days_held = current_idx - entry_idx
        entry_price = float(df.iloc[entry_idx]['open'])
        cur_row = df.iloc[current_idx]
        cur_close = float(cur_row['close'])
        pnl = (cur_close / entry_price - 1) * 100

        if pnl < -3:
            return True, 'stop_loss'
        if pnl > 5:
            return True, 'take_profit'
        # 回到区间中位以上就出 (反弹目标达成)
        if days_held >= 2:
            low_20 = float(df['low'].iloc[max(0, current_idx-20):current_idx+1].min())
            high_20 = float(df['high'].iloc[max(0, current_idx-20):current_idx+1].max())
            mid = (low_20 + high_20) / 2
            if cur_close > mid and pnl > 2:
                return True, 'target_achieved'
        if days_held >= 5:
            return True, 'time_exit'
        return False, ''


# ====================================================================
# S4: 供给吸收确认 (Supply Absorption)
# ====================================================================
class S4_SupplyAbsorption(Strategy):
    """问题：阻力位抛压被吸收了吗？
    量减+价不跌+实体收缩 = 抛压吸收，突破在即
    """
    name = 's4_supply_absorption'
    description = '供给吸收确认 - 阻力位量能递减+价格拒绝下跌'
    max_hold_days = 7

    def entry_signals(self, df, date_idx):
        if date_idx < 25:
            return False

        row = df.iloc[date_idx]
        close = float(row['close'])
        high = float(row['high'])
        low = float(row['low'])
        open_p = float(row['open'])

        # 识别阻力区: 过去20天最高价附近
        highs_20 = df['high'].values.astype(float)
        max_high = np.max(highs_20[date_idx-20:date_idx])
        resistance_zone = [max_high * 0.97, max_high * 1.01]

        # 已经在阻力区停留至少3根K线
        if not (resistance_zone[0] <= close <= resistance_zone[1]):
            return False

        if date_idx < 3:
            return False

        # 检查最近3根K线
        recent_vols = []
        recent_lows = []
        recent_bodies = []
        for i in range(date_idx - 2, date_idx + 1):
            r = df.iloc[i]
            recent_vols.append(float(r['volume']))
            recent_lows.append(float(r['low']))
            body = abs(float(r['close']) - float(r['open']))
            recent_bodies.append(body)

        # 成交量递减
        if len(recent_vols) >= 3 and recent_vols[0] > 0:
            if not (recent_vols[2] < recent_vols[0] * 0.70):
                return False
        else:
            return False

        # 最低价连续抬高
        if not (recent_lows[2] > recent_lows[1] > recent_lows[0]):
            return False

        # 第3根K线收在振幅上半区
        rng = high - low
        if rng > 0:
            close_position = (close - low) / rng
            if close_position < 0.55:
                return False

        # 实体缩小
        if recent_bodies[2] > recent_bodies[0]:
            return False

        return True

    def exit_signals(self, df, entry_idx, current_idx):
        days_held = current_idx - entry_idx
        entry_price = float(df.iloc[entry_idx]['open'])
        cur_close = float(df.iloc[current_idx]['close'])
        pnl = (cur_close / entry_price - 1) * 100

        if pnl < -3:
            return True, 'stop_loss'
        if pnl > 8:
            return True, 'take_profit'
        if days_held >= 2 and pnl < -1:
            return True, 'failed_breakout'
        if days_held >= 7:
            return True, 'time_exit'
        return False, ''


# ====================================================================
# S5: 连续推动力衰竭 (Push Exhaustion)
# ====================================================================
class S5_PushExhaustion(Strategy):
    """问题：连续推动后的宽幅量能是强烈体现还是力竭？
    连续阳线后出现极端K线却收不回高位 = 买方力竭
    """
    name = 's5_push_exhaustion'
    description = '连续推动力衰竭 - 三次推动后的极端宽幅反转'
    max_hold_days = 3
    stop_loss_pct = 0.02

    def entry_signals(self, df, date_idx):
        """此处为做空/观望信号
        实际做空在当前系统不适用, 因此本策略返回False代表做多开仓
        实际用途: 已做多时此信号出现应卖出
        """
        # 纯做空/观望策略, 不在多头系统中开仓
        return False

    def exit_signals(self, df, entry_idx, current_idx):
        """检测到推动衰竭应卖出"""
        if current_idx < 5:
            return False, ''

        # 前3-5根K线: 连续阳线
        yang_count = 0
        total_move = 1.0
        for i in range(current_idx, current_idx - 5, -1):
            if i < 0:
                break
            r = df.iloc[i]
            if float(r['close']) > float(r['open']):
                yang_count += 1
                total_move *= float(r['close']) / float(r['open'])
            else:
                break

        if yang_count < 3:
            return False, ''
        total_pct = (total_move - 1) * 100
        if total_pct < 5:
            return False, ''

        # 出现极端K线
        row = df.iloc[current_idx]
        rng = float(row['high']) - float(row['low'])
        prev_rngs = []
        for i in range(current_idx - 5, current_idx):
            if i < 0:
                continue
            r = df.iloc[i]
            prev_rngs.append(float(r['high']) - float(r['low']))

        avg_rng = np.mean(prev_rngs) if prev_rngs else 0
        if avg_rng <= 0 or rng < avg_rng * 1.6:
            return False, ''

        # 收盘在振幅下30% (力竭)
        if rng > 0:
            close_position = (float(row['close']) - float(row['low'])) / rng
            if close_position > 0.35:
                return False, ''

        # 成交量 > 前5均量2倍
        volumes = df['volume'].values.astype(float)
        if current_idx >= 5:
            prev_vol_mean = np.mean(volumes[current_idx-5:current_idx])
            if prev_vol_mean > 0 and volumes[current_idx] > prev_vol_mean * 2:
                return True, 'push_exhaustion'

        return False, ''


# ====================================================================
# S6: 板块同频确认 (Sector Sympathy)
# ====================================================================
class S6_SectorSympathy(Strategy):
    """问题：个股涨是因为板块涨，还是有独立alpha？
    板块同频 = 更稳定; 孤狼突破 = 更脆弱
    需要板块数据支持, 简化版使用当前全市场状态
    """
    name = 's6_sector_sympathy'
    description = '板块同频确认 - 有板块配合的突破信号更可靠'
    max_hold_days = 5
    stop_loss_pct = 0.04

    def __init__(self, params=None):
        super().__init__(params)
        self._sector_confidence = 0.5

    def set_sector_data(self, up_stock_count=0, total_stock_count=0,
                        sector_has_limit_up=True, sector_volume_ratio=1.0):
        """设置板块环境数据 (由外部传入)"""
        if total_stock_count > 0:
            self._sector_confidence = up_stock_count / total_stock_count
        if sector_has_limit_up:
            self._sector_confidence = max(self._sector_confidence, 0.6)
        if sector_volume_ratio > 1.3:
            self._sector_confidence = min(1.0, self._sector_confidence * 1.2)

    def entry_signals(self, df, date_idx):
        if date_idx < 5:
            return False

        # 板块环境过滤
        if self._sector_confidence < 0.4:
            return False

        # 个股自身条件: 放量上涨
        row = df.iloc[date_idx]
        if float(row['close']) <= float(row['open']):
            return False

        volumes = df['volume'].values.astype(float)
        if date_idx >= 5:
            prev_vol_mean = np.mean(volumes[date_idx-5:date_idx])
            if prev_vol_mean > 0 and volumes[date_idx] < prev_vol_mean * 1.2:
                return False

        return True

    def exit_signals(self, df, entry_idx, current_idx):
        days_held = current_idx - entry_idx
        entry_price = float(df.iloc[entry_idx]['open'])
        cur_close = float(df.iloc[current_idx]['close'])
        pnl = (cur_close / entry_price - 1) * 100

        if pnl < -4:
            return True, 'stop_loss'
        if pnl > 7:
            return True, 'take_profit'
        if days_held >= 5:
            return True, 'time_exit'
        return False, ''


# ====================================================================
# S7: 尾盘板块方向 (Sector EOD Directional Bias)
# ====================================================================
class S7_SectorEOD(Strategy):
    """问题：尾盘最强板块暴露了第二天资金的方向？
    尾盘量能集中的板块通常是资金在日末确认方向
    """
    name = 's7_sector_eod'
    description = '尾盘板块方向 - 尾盘资金最集中的板块'
    max_hold_days = 2

    def __init__(self, params=None):
        super().__init__(params)
        self._sector_eod_bias = 'neutral'

    def set_sector_bias(self, bias='neutral'):
        """设置板块方向偏好 (由外部传入)"""
        self._sector_eod_bias = bias

    def entry_signals(self, df, date_idx):
        if date_idx < 2:
            return False
        if self._sector_eod_bias == 'bearish':
            return False
        return True

    def exit_signals(self, df, entry_idx, current_idx):
        days_held = current_idx - entry_idx
        if days_held >= 2:
            return True, 'time_exit'
        entry_price = float(df.iloc[entry_idx]['open'])
        cur_close = float(df.iloc[current_idx]['close'])
        pnl = (cur_close / entry_price - 1) * 100
        if pnl < -3:
            return True, 'stop_loss'
        if pnl > 0:
            return True, 'take_profit_quick'
        return False, ''


# ====================================================================
# S8: 冰点逆向套利 (Ice Age Contrarian)
# ====================================================================
class S8_IceContrarian(Strategy):
    """问题：市场恐慌到极点时如何捕捉短期反弹？
    冰点期: 超跌+下影线+量枯竭 = 反弹前兆
    """
    name = 's8_ice_contrarian'
    description = '冰点逆向套利 - 恐慌极值的超跌反弹'
    max_hold_days = 2
    stop_loss_pct = 0.02

    def entry_signals(self, df, date_idx):
        if date_idx < 10:
            return False

        row = df.iloc[date_idx]
        close = float(row['close'])
        high = float(row['high'])
        low = float(row['low'])
        open_p = float(row['open'])
        volume = float(row['volume'])

        # 1. 相对前5日高点跌幅 > 10%
        highs_5 = df['high'].values.astype(float)
        max_high_5 = np.max(highs_5[date_idx-5:date_idx]) if date_idx >= 5 else high
        decline_pct = (close / max_high_5 - 1) * 100
        if decline_pct > -8:
            return False

        # 2. 下影线 >= 振幅40%
        rng = high - low
        if rng <= 0:
            return False
        lower_shadow = min(open_p, close) - low
        if lower_shadow / rng < 0.35:
            return False

        # 3. 成交量萎缩
        volumes = df['volume'].values.astype(float)
        if date_idx >= 5:
            prev_vol_mean = np.mean(volumes[date_idx-5:date_idx])
            if prev_vol_mean > 0 and volume > prev_vol_mean * 0.75:
                return False

        return True

    def exit_signals(self, df, entry_idx, current_idx):
        days_held = current_idx - entry_idx
        entry_price = float(df.iloc[entry_idx]['open'])
        cur_close = float(df.iloc[current_idx]['close'])
        pnl = (cur_close / entry_price - 1) * 100

        if pnl < -2:
            return True, 'stop_loss'
        if pnl > 4:
            return True, 'take_profit'
        if days_held >= 2:
            return True, 'time_exit'
        return False, ''


# ── 策略注册表 ──
ALL_STRATEGIES = {
    's1_eod_momentum': S1_EODMomentum,
    's2_volume_breakout': S2_VolumeBreakout,
    's3_range_reversal': S3_RangeReversal,
    's4_supply_absorption': S4_SupplyAbsorption,
    's5_push_exhaustion': S5_PushExhaustion,
    's6_sector_sympathy': S6_SectorSympathy,
    's7_sector_eod': S7_SectorEOD,
    's8_ice_contrarian': S8_IceContrarian,
}
