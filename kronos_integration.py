"""
Kronos K线大模型集成 — 用 KronosPredictor 做OHLCV预测 → 评分修正

KronosTokenizer是数值量化器，不是文本tokenizer。
正确的用法：数值OHLCV → normalize → quantize → transformer → dequantize → predict
"""
import os
import sys
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger('kronos')

MODEL_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kronos_local_cache')
TOKENIZER_PATH = os.path.join(MODEL_CACHE_DIR, 'tokenizer')
MODEL_PATH = os.path.join(MODEL_CACHE_DIR, 'model')
PRED_LEN = 5
LOOKBACK = 40

_predictor = None


def _load():
    global _predictor
    if _predictor is not None:
        return _predictor
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Kronos'))
        from model import Kronos, KronosTokenizer, KronosPredictor
        tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_PATH)
        model = Kronos.from_pretrained(MODEL_PATH)
        _predictor = KronosPredictor(model, tokenizer, device='cpu', max_context=1024)
        logger.info('Kronos loaded (KronosPredictor)')
        return _predictor
    except Exception as e:
        logger.warning(f'Kronos load failed: {e}')
        return None


def predict_kline(df):
    """KronosPredictor推理

    df: DataFrame with [open, close, high, low, volume] + 可选 date
    returns: predicted OHLCV DataFrame, or None
    """
    predictor = _load()
    if predictor is None or df is None or len(df) < LOOKBACK:
        return None

    try:
        recent = df.tail(LOOKBACK).copy()
        # 补齐amount
        if 'amount' not in recent.columns:
            recent['amount'] = recent['close'] * recent['volume']

        x_df = recent[['open', 'high', 'low', 'close', 'volume', 'amount']]

        # 时间戳：必须datetime类型
        if 'date' in recent.columns:
            x_ts = pd.to_datetime(recent['date'])
        else:
            x_ts = pd.date_range(end=datetime.now(), periods=len(x_df), freq='D')

        # 未来交易日
        last = x_ts.iloc[-1]
        future = []
        d = last
        while len(future) < PRED_LEN:
            d += timedelta(days=1)
            if d.weekday() < 5:
                future.append(d)
        y_ts = pd.Series(pd.to_datetime(future))

        pred_df = predictor.predict(
            df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
            pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=1, verbose=False,
        )
        if pred_df is not None and not pred_df.empty:
            return pred_df
        return None

    except Exception as e:
        logger.debug(f'Kronos predict: {e}')
        return None


def score_kronos_prediction(code, df):
    """Kronos预测 → 评分修正 (-10 ~ +15)"""
    pred = predict_kline(df)
    if pred is None:
        return 0, ''

    try:
        last_close = float(df['close'].iloc[-1])
        pred_close = float(pred['close'].iloc[-1])
        pred_chg = (pred_close / last_close - 1) * 100

        if pred_chg > 5:     return 12, f'Kronos预测涨{pred_chg:.1f}%(+12)'
        elif pred_chg > 3:   return 8,  f'Kronos预测涨{pred_chg:.1f}%(+8)'
        elif pred_chg > 1:   return 4,  f'Kronos预测涨{pred_chg:.1f}%(+4)'
        elif pred_chg > 0:   return 1,  f'Kronos预测微涨{pred_chg:.1f}%(+1)'
        elif pred_chg > -2:  return -2, f'Kronos预测跌{pred_chg:.1f}%(-2)'
        elif pred_chg > -5:  return -5, f'Kronos预测跌{pred_chg:.1f}%(-5)'
        else:                return -8, f'Kronos预测大跌{pred_chg:.1f}%(-8)'
    except Exception as e:
        logger.debug(f'score_kronos: {e}')
        return 0, ''


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    from data import get_stock_daily_cached
    df = get_stock_daily_cached('000001', 60)
    if df is not None:
        score, reason = score_kronos_prediction('000001', df)
        print(f'Kronos: {score} ({reason})')
