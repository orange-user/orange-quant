"""Pulse Orange v4 感知层 (Layer 2)
市场环境分类: 情绪周期 + 趋势状态 + 波动率环境
零指标: 使用OHLCV原始数据的统计分析
"""
import sys, os, json, logging
from datetime import datetime, date
from typing import Optional
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR

logger = logging.getLogger('v4.perception')

# ── 情绪周期常量 ──
REGIME_LABELS = ['ice_age', 'retreat', 'fermentation', 'climax', 'overheated']
REGIME_CN = {
    'ice_age': '冰点', 'retreat': '退潮', 'fermentation': '发酵',
    'climax': '高潮', 'overheated': '过热', 'unknown': '未知'
}
REGIME_COLORS = {
    'ice_age': '#2196F3', 'retreat': '#9E9E9E', 'fermentation': '#4CAF50',
    'climax': '#FF9800', 'overheated': '#F44336', 'unknown': '#607D8B'
}


# ====================================================================
# 1. 情绪周期分类器 (基于成交量涨跌停板数据)
# ====================================================================
class EmotionCycleClassifier:
    """基于三组独立信号的多数投票情绪分类器
    信号组1: 涨停结构 (涨停家数/炸板率/连板高度/晋级率)
    信号组2: 量价结构 (沪深300价格位置/量能)
    信号组3: 市场宽度 (涨跌家数/创N日新低新高)
    """
    # 涨停家数阈值
    LIMIT_UP_THRESHOLDS = [20, 50, 80, 120]   # 冰点/退潮/发酵/高潮/过热
    # 炸板率阈值(反向)
    ZHADABAN_THRESHOLDS = [0.40, 0.30, 0.20, 0.10]
    # 连板高度阈值
    LIANBAN_THRESHOLDS = [2, 3, 5, 8]
    # 晋级率阈值
    JINJI_THRESHOLDS = [0.15, 0.25, 0.40, 0.60]

    def __init__(self):
        self.cache = {}

    def classify_from_data(self, limit_up_count=0, limit_down_count=0,
                           zhadaban_rate=0.5, max_lianban=0, jinji_rate=0,
                           index_position=0.0, volume_ratio=1.0,
                           up_down_ratio=0.5, index_above_ma20=True,
                           new_high_count=0, new_low_count=0,
                           above_ma20_pct=0.5) -> dict:
        """从实时/历史数据分类市场情绪

        Args:
            limit_up_count: 涨停家数
            limit_down_count: 跌停家数
            zhadaban_rate: 炸板率 (0-1)
            max_lianban: 市场最高连板数
            jinji_rate: 首板→二板晋级率 (0-1)
            index_position: 沪深300在20日区间中的位置 (0-1)
            volume_ratio: 沪深300量比 (= 成交量/20日均量)
            up_down_ratio: 上涨家数占比 (0-1)
            index_above_ma20: 沪深300是否在20日均线上方
            new_high_count: 创20日新高家数
            new_low_count: 创20日新低家数
            above_ma20_pct: 全市场在20日均线上方占比 (0-1)
        """
        # ── 信号1: 涨停结构 ──
        if limit_up_count >= 120:
            s1 = 'overheated'
        elif limit_up_count >= 80:
            s1 = 'climax'
        elif limit_up_count >= 50:
            s1 = 'fermentation'
        elif limit_up_count >= 20:
            s1 = 'retreat'
        else:
            s1 = 'ice_age'

        # 炸板率修正: 高炸板率即使涨停多也可能是分歧
        if zhadaban_rate > 0.40 and s1 in ('climax', 'overheated'):
            s1 = 'fermentation'
        if zhadaban_rate < 0.15 and s1 == 'retreat':
            s1 = 'fermentation'

        # 连板高度修正
        if max_lianban <= 2:
            if s1 in ('climax', 'overheated'):
                s1 = 'fermentation'
        if max_lianban >= 7:
            if s1 in ('fermentation', 'retreat'):
                s1 = 'climax'

        # ── 信号2: 量价结构 ──
        if index_above_ma20:
            if volume_ratio > 1.3 and up_down_ratio > 0.6:
                s2 = 'climax'
            elif volume_ratio > 0.8:
                s2 = 'fermentation'
            else:
                s2 = 'retreat'
        else:
            if up_down_ratio < 0.3:
                s2 = 'ice_age'
            elif volume_ratio < 0.7:
                s2 = 'retreat'
            elif new_low_count > new_high_count * 2:
                s2 = 'retreat'
            else:
                s2 = 'fermentation'

        # ── 信号3: 市场宽度 ──
        if above_ma20_pct > 0.7 and up_down_ratio > 0.6:
            s3 = 'climax'
        elif above_ma20_pct > 0.5:
            s3 = 'fermentation'
        elif above_ma20_pct > 0.3:
            if new_low_count > 50:
                s3 = 'retreat'
            else:
                s3 = 'fermentation'
        else:
            s3 = 'ice_age'

        # ── 多数投票 ──
        votes = [s1, s2, s3]
        vote_order = ['ice_age', 'retreat', 'fermentation', 'climax', 'overheated']
        vote_scores = {r: 0 for r in vote_order}
        for v in votes:
            if v in vote_scores:
                vote_scores[v] += 1

        max_vote = max(vote_scores.values())
        winners = [r for r, c in vote_scores.items() if c == max_vote]

        if len(winners) == 1:
            regime = winners[0]
        elif len(winners) == 2:
            # 取中间位置 (保守策略: 偏向发酵/退潮)
            idxs = [vote_order.index(w) for w in winners]
            regime = vote_order[min(idxs) + (max(idxs) - min(idxs)) // 2]
        else:
            regime = 'fermentation'

        confidence = max_vote / 3.0

        return {
            'regime': regime,
            'regime_cn': REGIME_CN.get(regime, '未知'),
            'confidence': round(confidence, 2),
            'votes': {'s1_zhangting': s1, 's2_liangjia': s2, 's3_kuandu': s3},
            'raw': {
                'limit_up': limit_up_count,
                'limit_down': limit_down_count,
                'zhadaban_rate': round(zhadaban_rate, 3),
                'max_lianban': max_lianban,
                'jinji_rate': round(jinji_rate, 3),
                'up_down_ratio': round(up_down_ratio, 3),
                'above_ma20_pct': round(above_ma20_pct, 3),
            }
        }

    def classify_live(self, date_str=None) -> dict:
        """从akshare获取实时数据分类"""
        try:
            import akshare as ak
            if date_str is None:
                date_str = datetime.now().strftime('%Y%m%d')

            # 涨停池
            zt = ak.stock_zt_pool_em(date=date_str)
            limit_up_count = len(zt) if zt is not None else 0

            # 跌停池
            try:
                dt = ak.stock_zt_pool_dtgc_em(date=date_str)
                limit_down_count = len(dt) if dt is not None else 0
            except:
                limit_down_count = 0

            # 炸板率: 近似估算
            try:
                zbgc = ak.stock_zt_pool_zbgc_em(date=date_str)
                zhadaban = len(zbgc) if zbgc is not None else 0
                zhadaban_rate = zhadaban / (limit_up_count + zhadaban + 1)
            except:
                zhadaban_rate = 0.5

            # 连板高度: 从涨停池中找连板数最高的
            max_lianban = 0
            if zt is not None and '连续涨停' in zt.columns:
                max_lianban = int(zt['连续涨停'].max()) if not zt['连续涨停'].empty else 0
            elif zt is not None and 'board' in zt.columns:
                max_lianban = int(zt['board'].max()) if not zt['board'].empty else 0

            # 沪深300数据
            from data import get_index_daily
            idx = get_index_daily('sh000300', 30)
            index_position = 0.5
            volume_ratio = 1.0
            index_above_ma20 = True
            if idx is not None and len(idx) > 20:
                close = float(idx['close'].iloc[-1])
                ma20 = float(idx['close'].rolling(20).mean().iloc[-1])
                low_20 = float(idx['close'].iloc[-20:].min())
                high_20 = float(idx['close'].iloc[-20:].max())
                index_position = (close - low_20) / (high_20 - low_20) if high_20 > low_20 else 0.5
                vol_ma20 = idx['volume'].rolling(20).mean().iloc[-1] if 'volume' in idx.columns else 1
                volume_ratio = float(idx['volume'].iloc[-1] / vol_ma20) if vol_ma20 > 0 else 1.0
                index_above_ma20 = close > ma20

            # 上涨家数占比 (从实时行情估算)
            up_down_ratio = 0.5
            try:
                spot = ak.stock_zh_a_spot_em()
                if spot is not None:
                    up_count = len(spot[spot['涨跌幅'] > 0]) if '涨跌幅' in spot.columns else len(spot) // 2
                    down_count = len(spot[spot['涨跌幅'] < 0]) if '涨跌幅' in spot.columns else len(spot) // 2
                    total = up_count + down_count
                    up_down_ratio = up_count / total if total > 0 else 0.5
            except:
                pass

            # 均线上方占比 (使用全市场行情数据估算)
            above_ma20_pct = 0.5
            try:
                if spot is not None and all(c in spot.columns for c in ['今开', '昨收']):
                    above_ma20_pct = len(spot[spot['今开'] > spot['昨收'] * 0.98]) / len(spot)
            except:
                pass

            return self.classify_from_data(
                limit_up_count=limit_up_count,
                limit_down_count=limit_down_count,
                zhadaban_rate=zhadaban_rate,
                max_lianban=max_lianban,
                jinji_rate=0.3,
                index_position=index_position,
                volume_ratio=volume_ratio,
                up_down_ratio=up_down_ratio,
                index_above_ma20=index_above_ma20,
                above_ma20_pct=above_ma20_pct,
            )

        except Exception as e:
            logger.warning(f"情绪分类实时获取失败: {e}")
            return self.classify_from_data()

    def classify_historical(self, df: pd.DataFrame, date_idx: int,
                            lookback=20) -> dict:
        """从历史DataFrame的某一天估算情绪
        用于回测中的情绪周期重建
        Args:
            df: 沪深300日线DataFrame (含open/close/high/low/volume)
            date_idx: 当前日在DataFrame中的索引
            lookback: 回溯天数
        """
        if df is None or len(df) < lookback or date_idx < lookback:
            return self.classify_from_data()

        window = df.iloc[date_idx - lookback:date_idx + 1]

        close = float(window['close'].iloc[-1])
        closes = window['close'].values.astype(float)
        low_20 = float(window['low'].min())
        high_20 = float(window['high'].max())

        index_position = (close - low_20) / (high_20 - low_20) if high_20 > low_20 else 0.5
        ma20 = float(window['close'].mean())
        index_above_ma20 = close > ma20

        # 量比
        volumes = window['volume'].values.astype(float)
        vol_ma20 = float(np.mean(volumes))
        volume_ratio = float(volumes[-1] / vol_ma20) if vol_ma20 > 0 else 1.0

        # 涨跌幅分布估算上涨家数占比
        returns = np.diff(closes) / closes[:-1]
        up_ratio = float(np.mean(returns > 0))

        # 20日振幅
        amplitude = (high_20 - low_20) / low_20

        # 用振幅估算市场宽度
        above_ma20_pct = min(1.0, max(0.1, 0.5 + (close / ma20 - 1) * 5))

        return self.classify_from_data(
            index_position=index_position,
            volume_ratio=volume_ratio,
            up_down_ratio=up_ratio,
            index_above_ma20=index_above_ma20,
            new_high_count=int(up_ratio * 50),
            new_low_count=int((1 - up_ratio) * 50),
            above_ma20_pct=above_ma20_pct,
        )


# ====================================================================
# 2. 趋势状态分类器 (基于价格结构)
# ====================================================================
class TrendClassifier:
    """基于裸K结构判定趋势/震荡/过渡
    不使用任何加权指标, 仅使用K线结构特征
    """
    def classify(self, df: pd.DataFrame, date_idx: int,
                 lookback=20) -> dict:
        """分类某日的趋势状态

        Returns:
            dict: {'trend': 'strong_trend'|'weak_trend'|'oscillation'|'transition',
                   'direction': 'up'|'down'|'none',
                   'confidence': 0-1}
        """
        if df is None or len(df) < lookback or date_idx < lookback:
            return {'trend': 'transition', 'direction': 'none', 'confidence': 0.0}

        window = df.iloc[date_idx - lookback:date_idx + 1]
        highs = window['high'].values.astype(float)
        lows = window['low'].values.astype(float)
        closes = window['close'].values.astype(float)
        opens = window['open'].values.astype(float)
        volumes = window['volume'].values.astype(float)

        # ── 特征1: 趋势K线占比 ──
        trend_bars = 0
        total_bars = len(window) - 1
        for i in range(1, len(window)):
            body = abs(closes[i] - opens[i])
            rng = highs[i] - lows[i]
            if rng <= 0:
                continue
            close_pos = (closes[i] - lows[i]) / rng
            # 趋势K线: 实体>50%且在自身半区
            if body / rng > 0.50:
                if closes[i] >= opens[i] and close_pos >= 0.50:
                    trend_bars += 1
                elif closes[i] < opens[i] and close_pos <= 0.50:
                    trend_bars += 1
        trend_ratio = trend_bars / total_bars if total_bars > 0 else 0

        # ── 特征2: 价格区间位置 ──
        low_20 = np.min(lows)
        high_20 = np.max(highs)
        cur_close = closes[-1]
        pos_in_range = (cur_close - low_20) / (high_20 - low_20) if high_20 > low_20 else 0.5

        # ── 特征3: 高点递进 ──
        higher_highs = sum(1 for i in range(1, len(highs))
                           if highs[i] > highs[i-1])
        higher_lows = sum(1 for i in range(1, len(lows))
                          if lows[i] > lows[i-1])
        hh_ratio = higher_highs / (len(highs) - 1) if len(highs) > 1 else 0.5
        hl_ratio = higher_lows / (len(lows) - 1) if len(lows) > 1 else 0.5

        # ── 特征4: 成交量结构 ──
        vol_half = len(volumes) // 2
        vol_first_half = np.mean(volumes[:vol_half]) if vol_half > 0 else 1
        vol_second_half = np.mean(volumes[vol_half:]) if vol_half < len(volumes) else 1
        vol_trend = vol_second_half / vol_first_half if vol_first_half > 0 else 1.0

        # ── 判定 ──
        # 强趋势: 趋势K线>60% + 高点递进>60% + 收盘在区间上部>60%
        if trend_ratio > 0.60 and hh_ratio > 0.60 and pos_in_range > 0.60:
            direction = 'up' if hl_ratio > 0.5 else 'down'
            confidence = min(1.0, (trend_ratio + hh_ratio + pos_in_range) / 2.5)
            return {'trend': 'strong_trend', 'direction': direction,
                    'confidence': round(confidence, 2)}
        elif trend_ratio > 0.60 and hh_ratio > 0.60 and pos_in_range < 0.40:
            direction = 'down' if hl_ratio < 0.5 else 'up'
            confidence = min(1.0, (trend_ratio + (1 - hh_ratio)) / 2.0)
            return {'trend': 'strong_trend', 'direction': direction,
                    'confidence': round(confidence, 2)}

        # 弱趋势: 趋势K线<60%但>40% + 高点递进一般
        if trend_ratio > 0.40 and (hh_ratio > 0.50 or hl_ratio > 0.55):
            direction = 'up' if pos_in_range > 0.50 else 'down'
            confidence = (trend_ratio + max(hh_ratio, hl_ratio)) / 2.5
            return {'trend': 'weak_trend', 'direction': direction,
                    'confidence': round(min(1.0, confidence), 2)}

        # 震荡: 区间处于中间50% + 高低点递进不明显
        if 0.25 < pos_in_range < 0.75 and hh_ratio < 0.60:
            return {'trend': 'oscillation', 'direction': 'none',
                    'confidence': round(0.7 - abs(pos_in_range - 0.5), 2)}

        # 过渡
        return {'trend': 'transition', 'direction': 'none',
                'confidence': round(0.4, 2)}


# ====================================================================
# 3. 波动率环境分类器
# ====================================================================
class VolatilityClassifier:
    """基于ATR百分位的波动率环境分类"""
    def classify(self, df: pd.DataFrame, date_idx: int,
                 lookback=60) -> dict:
        """分类波动率环境

        Returns:
            dict: {'volatility': 'low'|'normal'|'high'|'extreme',
                   'atr_percentile': 0-1,
                   'amplitude_20d': float}
        """
        if df is None or len(df) < lookback or date_idx < lookback:
            return {'volatility': 'normal', 'atr_percentile': 0.5}

        window = df.iloc[date_idx - lookback:date_idx + 1]
        highs = window['high'].values.astype(float)
        lows = window['low'].values.astype(float)
        closes = window['close'].values.astype(float)

        # 计算每日振幅
        amplitudes = (highs - lows) / closes
        cur_amplitude = amplitudes[-1]
        amp_percentile = np.mean(amplitudes < cur_amplitude)

        if amp_percentile > 0.90:
            vol = 'extreme'
        elif amp_percentile > 0.75:
            vol = 'high'
        elif amp_percentile < 0.20:
            vol = 'low'
        else:
            vol = 'normal'

        return {
            'volatility': vol,
            'atr_percentile': round(amp_percentile, 2),
            'amplitude_20d': round(float(np.mean(amplitudes[-20:]) * 100), 2),
        }


# ====================================================================
# 4. 综合感知层
# ====================================================================
class MarketPerception:
    """感知层综合器: 整合情绪+趋势+波动率"""

    def __init__(self):
        self.emotion = EmotionCycleClassifier()
        self.trend = TrendClassifier()
        self.volatility = VolatilityClassifier()

    def perceive(self, idx_df=None, date_idx=None, live_data=None) -> dict:
        """综合感知市场状态

        Args:
            idx_df: 沪深300日线DataFrame (用于回测)
            date_idx: 当前日在DataFrame中的索引 (用于回测)
            live_data: 实时数据的dict (用于实盘)
        Returns:
            dict: 包含regime/trend/volatility的综合感知结果
        """
        # 情绪周期
        if live_data:
            emotion_result = self.emotion.classify_from_data(**live_data)
        elif idx_df is not None and date_idx is not None:
            emotion_result = self.emotion.classify_historical(idx_df, date_idx)
        else:
            emotion_result = self.emotion.classify_live()

        # 趋势状态
        if idx_df is not None and date_idx is not None:
            trend_result = self.trend.classify(idx_df, date_idx)
        else:
            trend_result = {'trend': 'transition', 'direction': 'none',
                            'confidence': 0.5}

        # 波动率环境
        if idx_df is not None and date_idx is not None:
            vol_result = self.volatility.classify(idx_df, date_idx)
        else:
            vol_result = {'volatility': 'normal', 'atr_percentile': 0.5}

        # ── 综合判定是否可交易 ──
        regime = emotion_result.get('regime', 'unknown')
        trend_type = trend_result.get('trend', 'transition')
        vol_type = vol_result.get('volatility', 'normal')

        # 可交易判定
        trade_allowed = True
        block_reason = ""

        if regime in ('ice_age',):
            trade_allowed = False
            block_reason = "冰点期: 强制关闭所有买入"
        elif regime == 'retreat' and trend_type == 'weak_trend':
            trade_allowed = False
            block_reason = "退潮+弱趋势: 不开新仓"
        elif vol_type == 'extreme':
            trade_allowed = False
            block_reason = "极端波动: 暂停交易"

        return {
            'regime': regime,
            'regime_cn': REGIME_CN.get(regime, '未知'),
            'regime_confidence': emotion_result.get('confidence', 0),
            'trend': trend_type,
            'trend_direction': trend_result.get('direction', 'none'),
            'trend_confidence': trend_result.get('confidence', 0),
            'volatility': vol_type,
            'volatility_percentile': vol_result.get('atr_percentile', 0.5),
            'trade_allowed': trade_allowed,
            'block_reason': block_reason,
            'emotion_detail': emotion_result.get('raw', {}),
            'votes': emotion_result.get('votes', {}),
        }


# ====================================================================
# 环境→策略匹配矩阵
# ====================================================================
STRATEGY_REGIME_MATRIX = {
    # strategy_name -> {regime: 'enable'|'disable'|'caution'}
    's1_eod_momentum': {
        'ice_age': 'disable', 'retreat': 'disable', 'fermentation': 'enable',
        'climax': 'enable', 'overheated': 'disable',
    },
    's2_volume_breakout': {
        'ice_age': 'disable', 'retreat': 'disable', 'fermentation': 'enable',
        'climax': 'enable', 'overheated': 'disable',
    },
    's3_range_reversal': {
        'ice_age': 'disable', 'retreat': 'enable', 'fermentation': 'disable',
        'climax': 'disable', 'overheated': 'disable',
    },
    's4_supply_absorption': {
        'ice_age': 'disable', 'retreat': 'disable', 'fermentation': 'enable',
        'climax': 'enable', 'overheated': 'disable',
    },
    's5_push_exhaustion': {
        'ice_age': 'enable', 'retreat': 'enable', 'fermentation': 'disable',
        'climax': 'disable', 'overheated': 'enable',
    },
    's6_sector_sympathy': {
        'ice_age': 'disable', 'retreat': 'disable', 'fermentation': 'enable',
        'climax': 'enable', 'overheated': 'disable',
    },
    's7_sector_eod': {
        'ice_age': 'disable', 'retreat': 'disable', 'fermentation': 'enable',
        'climax': 'enable', 'overheated': 'disable',
    },
    's8_ice_contrarian': {
        'ice_age': 'enable', 'retreat': 'disable', 'fermentation': 'disable',
        'climax': 'disable', 'overheated': 'disable',
    },
}

# 趋势状态匹配
STRATEGY_TREND_MATRIX = {
    's1_eod_momentum': {'strong_trend': 'enable', 'weak_trend': 'enable',
                        'oscillation': 'disable', 'transition': 'caution'},
    's2_volume_breakout': {'strong_trend': 'enable', 'weak_trend': 'enable',
                           'oscillation': 'disable', 'transition': 'caution'},
    's3_range_reversal': {'strong_trend': 'disable', 'weak_trend': 'disable',
                          'oscillation': 'enable', 'transition': 'caution'},
    's4_supply_absorption': {'strong_trend': 'enable', 'weak_trend': 'enable',
                             'oscillation': 'caution', 'transition': 'caution'},
    's5_push_exhaustion': {'strong_trend': 'enable', 'weak_trend': 'caution',
                           'oscillation': 'disable', 'transition': 'disable'},
    's6_sector_sympathy': {'strong_trend': 'enable', 'weak_trend': 'enable',
                           'oscillation': 'enable', 'transition': 'disable'},
    's7_sector_eod': {'strong_trend': 'enable', 'weak_trend': 'enable',
                      'oscillation': 'disable', 'transition': 'disable'},
    's8_ice_contrarian': {'strong_trend': 'disable', 'weak_trend': 'disable',
                          'oscillation': 'enable', 'transition': 'enable'},
}


def is_strategy_allowed(strategy_name: str, perception: dict) -> tuple:
    """检查策略在当前市场环境下是否允许

    Returns:
        (allowed: bool, reason: str)
    """
    regime = perception.get('regime', 'unknown')
    trend = perception.get('trend', 'transition')

    # 检查情绪周期
    regime_status = STRATEGY_REGIME_MATRIX.get(strategy_name, {}).get(regime, 'disable')

    # 检查趋势状态
    trend_status = STRATEGY_TREND_MATRIX.get(strategy_name, {}).get(trend, 'disable')

    if regime_status == 'disable' or trend_status == 'disable':
        return False, f"环境[{regime}+{trend}]下禁用"

    if regime_status == 'caution' or trend_status == 'caution':
        return False, f"环境[{regime}+{trend}]下谨慎(暂不开仓)"

    return True, ""


def get_enabled_strategies(perception: dict, all_strategy_names: list) -> list:
    """获取当前环境所有启用策略"""
    enabled = []
    for sname in all_strategy_names:
        allowed, _ = is_strategy_allowed(sname, perception)
        if allowed:
            enabled.append(sname)
    return enabled
