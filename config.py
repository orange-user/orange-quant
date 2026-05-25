import os
import json
import datetime
import threading
import logging

DATA_DIR = 'data'
DB_PATH = os.path.join(DATA_DIR, 'stock_cache.db')
POSITION_FILE = os.path.join(DATA_DIR, 'positions.json')
TRADE_FILE = os.path.join(DATA_DIR, 'trades.json')
DIARY_FILE = os.path.join(DATA_DIR, 'diary.json')
BLACKLIST_FILE = os.path.join(DATA_DIR, 'blacklist.json')
FACTOR_STATS_FILE = os.path.join(DATA_DIR, 'factor_stats.json')
BACKTEST_FILE = os.path.join(DATA_DIR, 'backtest_results.json')
SIGNALS_LOG_FILE = os.path.join(DATA_DIR, 'signals_log.json')
FACTOR_REGISTRY_FILE = os.path.join(DATA_DIR, 'factor_registry.json')

os.makedirs(DATA_DIR, exist_ok=True)

# ==================== 日志 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(DATA_DIR, 'quant.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('quant')

# ==================== 线程安全JSON读写 ====================
_json_lock = threading.Lock()

def load_json(path, default=None):
    if default is None:
        default = [] if any(x in path for x in ['positions','trades','diary','blacklist','signals']) else {}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default


def save_json(data, path):
    """原子写入：先写.tmp再rename，避免写半截崩溃丢数据"""
    tmp_path = path + '.tmp'
    with _json_lock:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def save_transaction(positions, trades):
    """原子写入仓位+交易两个文件"""
    pos_tmp = POSITION_FILE + '.tmp'
    trd_tmp = TRADE_FILE + '.tmp'
    with _json_lock:
        with open(pos_tmp, 'w', encoding='utf-8') as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
        with open(trd_tmp, 'w', encoding='utf-8') as f:
            json.dump(trades, f, ensure_ascii=False, indent=2)
        os.replace(pos_tmp, POSITION_FILE)
        os.replace(trd_tmp, TRADE_FILE)

# ==================== 多因子注册表 ====================
FACTOR_REGISTRY = {
    "close":       {"category": "量价", "field": "close",       "description": "收盘价",                        "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "open":        {"category": "量价", "field": "open",        "description": "开盘价",                        "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "high":        {"category": "量价", "field": "high",        "description": "最高价",                        "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "low":         {"category": "量价", "field": "low",         "description": "最低价",                        "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "volume":      {"category": "量价", "field": "volume",      "description": "成交量",                        "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "turnover":    {"category": "量价", "field": "turnover",    "description": "换手率",                        "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "pe_ttm":      {"category": "估值", "field": "pe_ttm",      "description": "市盈率TTM",                     "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "pb_ttm":      {"category": "估值", "field": "pb_ttm",      "description": "市净率TTM",                     "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "ps_ttm":      {"category": "估值", "field": "ps_ttm",      "description": "市销率TTM",                     "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "market_cap":  {"category": "估值", "field": "market_cap",  "description": "总市值",                        "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "dividend_yield": {"category": "估值", "field": "dividend_yield", "description": "股息率",                 "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "roe":         {"category": "质量", "field": "roe",         "description": "净资产收益率ROE",               "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "roa":         {"category": "质量", "field": "roa",         "description": "总资产收益率ROA",               "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "gross_profit_margin": {"category": "质量", "field": "gross_profit_margin", "description": "毛利率",    "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "net_profit_margin":   {"category": "质量", "field": "net_profit_margin",   "description": "净利率",    "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "revenue_yoy":    {"category": "成长", "field": "revenue_yoy",    "description": "营收同比增长率",          "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "net_profit_yoy": {"category": "成长", "field": "net_profit_yoy", "description": "净利润同比增长率",         "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "eps_yoy":        {"category": "成长", "field": "eps_yoy",        "description": "每股收益同比增长率",       "formula": None, "weight": 1.0, "ic_30d": 0, "active": True},
    "momentum_1m": {"category": "动量", "field": None, "description": "近1月涨跌幅",  "formula": "close / close.shift(21) - 1",          "weight": 1.0, "ic_30d": 0, "active": True},
    "momentum_3m": {"category": "动量", "field": None, "description": "近3月涨跌幅",  "formula": "close / close.shift(63) - 1",          "weight": 1.0, "ic_30d": 0, "active": True},
    "momentum_6m": {"category": "动量", "field": None, "description": "近6月涨跌幅",  "formula": "close / close.shift(126) - 1",         "weight": 1.0, "ic_30d": 0, "active": True},
    "volatility_20d": {"category": "波动", "field": None, "description": "20日波动率", "formula": "returns.rolling(20).std()",              "weight": 1.0, "ic_30d": 0, "active": True},
    "amplitude_20d":  {"category": "波动", "field": None, "description": "20日均振幅", "formula": "(high/low-1).rolling(20).mean() * 100",   "weight": 1.0, "ic_30d": 0, "active": True},
    "volume_ratio_20": {"category": "资金", "field": None, "description": "20日量比",  "formula": "volume / volume.rolling(20).mean()",     "weight": 1.0, "ic_30d": 0, "active": True},
    "money_flow_5d":   {"category": "资金", "field": None, "description": "5日资金流向", "formula": "(close*volume).diff(5) / (close.shift(5)*volume.shift(5))", "weight": 1.0, "ic_30d": 0, "active": True},
    # ===== 新增：技术指标因子 =====
    "rsi_14":          {"category": "技术", "field": None, "description": "14日RSI", "formula": "rsi(14)", "weight": 1.0, "ic_30d": 0, "active": True},
    "macd_dif":        {"category": "技术", "field": None, "description": "MACD快线", "formula": "ema(12)-ema(26)", "weight": 1.0, "ic_30d": 0, "active": True},
    "kdj_j":           {"category": "技术", "field": None, "description": "KDJ-J值", "formula": "3*k-2*d", "weight": 1.0, "ic_30d": 0, "active": True},
    "boll_position":   {"category": "技术", "field": None, "description": "布林带位置", "formula": "(close-lower)/(upper-lower)", "weight": 1.0, "ic_30d": 0, "active": True},
    "atr_14":          {"category": "技术", "field": None, "description": "14日真实波幅", "formula": "tr.rolling(14).mean()/close", "weight": 1.0, "ic_30d": 0, "active": True},
    "ma5_slope":       {"category": "技术", "field": None, "description": "MA5斜率", "formula": "ma5/ma5.shift(5)-1", "weight": 1.0, "ic_30d": 0, "active": True},
    # ===== 新增：资金流向因子 =====
    "north_flow_5d":   {"category": "资金", "field": None, "description": "5日北向资金变化", "formula": "north_flow.diff(5)", "weight": 1.0, "ic_30d": 0, "active": True},
    "inst_ratio":      {"category": "资金", "field": None, "description": "机构持仓比例", "formula": "inst_holding/market_cap", "weight": 1.0, "ic_30d": 0, "active": True},
    "margin_ratio":    {"category": "资金", "field": None, "description": "融资买入占比", "formula": "margin_buy/total_volume", "weight": 1.0, "ic_30d": 0, "active": True},
    # ===== 新增：风险调整因子 =====
    "sharpe_20d":      {"category": "风险", "field": None, "description": "20日夏普比率", "formula": "returns.mean()/returns.std()*sqrt(252)", "weight": 1.0, "ic_30d": 0, "active": True},
    "max_dd_60d":      {"category": "风险", "field": None, "description": "60日最大回撤", "formula": "min(cummax_return)", "weight": 1.0, "ic_30d": 0, "active": True},
    "beta_60d":        {"category": "风险", "field": None, "description": "60日Beta系数", "formula": "cov(ret,idx_ret)/var(idx_ret)", "weight": 1.0, "ic_30d": 0, "active": True},
    "alpha_20d":       {"category": "风险", "field": None, "description": "20日Alpha", "formula": "ret-rf-beta*(idx_ret-rf)", "weight": 1.0, "ic_30d": 0, "active": True},
    "upside_vol_20d":  {"category": "风险", "field": None, "description": "上行波动率", "formula": "pos_returns.std()*sqrt(252)", "weight": 1.0, "ic_30d": 0, "active": True},
    "downside_vol_20d":{"category": "风险", "field": None, "description": "下行波动率", "formula": "neg_returns.std()*sqrt(252)", "weight": 1.0, "ic_30d": 0, "active": True},
    # ===== 新增：质量与成长因子 =====
    "peg_ttm":         {"category": "质量", "field": None, "description": "PEG指标", "formula": "pe_ttm/net_profit_yoy", "weight": 1.0, "ic_30d": 0, "active": True},
    "roe_5y_avg":      {"category": "质量", "field": None, "description": "5年平均ROE", "formula": "roe.rolling(5).mean()", "weight": 1.0, "ic_30d": 0, "active": True},
    "accrual_ratio":   {"category": "质量", "field": None, "description": "应计利润比", "formula": "(net_income-cfo)/total_assets", "weight": 1.0, "ic_30d": 0, "active": True},
    "revision_1m":     {"category": "情绪", "field": None, "description": "近1月盈利预测上调", "formula": "eps_est/eps_est.shift(21)-1", "weight": 1.0, "ic_30d": 0, "active": True},
    # ===== 新增：量价形态因子 =====
    "gap_ratio":       {"category": "量价", "field": None, "description": "跳空缺口比例", "formula": "(open-close.shift(1))/close.shift(1)", "weight": 1.0, "ic_30d": 0, "active": True},
    "volume_breakout": {"category": "量价", "field": None, "description": "放量突破信号", "formula": "volume/volume.rolling(20).mean()", "weight": 1.0, "ic_30d": 0, "active": True},
    "consecutive_yang":{"category": "量价", "field": None, "description": "连阳天数", "formula": "count(close>open)", "weight": 1.0, "ic_30d": 0, "active": True},
    "ma_convergence":  {"category": "量价", "field": None, "description": "均线粘合度", "formula": "1-std(ma5,ma10,ma20)/mean(ma5,ma10,ma20)", "weight": 1.0, "ic_30d": 0, "active": True},
}

DYNAMIC_FACTORS = {}
FACTOR_PERFORMANCE = {}
WEIGHT_LAST_UPDATE = None
INDICATOR_CYCLE_CACHE = {}


def load_json(path, default=None):
    if default is None:
        default = [] if any(x in path for x in ['positions','trades','diary','blacklist','signals']) else {}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default


def save_json(data, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_registry():
    if os.path.exists(FACTOR_REGISTRY_FILE):
        saved = load_json(FACTOR_REGISTRY_FILE, {})
        for k, v in saved.items():
            if k in FACTOR_REGISTRY:
                FACTOR_REGISTRY[k].update(v)
            else:
                FACTOR_REGISTRY[k] = v


def save_registry():
    save_json(FACTOR_REGISTRY, FACTOR_REGISTRY_FILE)


def get_weekday():
    return datetime.datetime.now().weekday()
