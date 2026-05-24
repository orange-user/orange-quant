import os
import json
import datetime

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
