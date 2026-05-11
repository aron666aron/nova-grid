# -*- coding: utf-8 -*-
"""量化交易系统 - 全局配置（双向网格 + 永续合约）"""
import os
from dotenv import load_dotenv

load_dotenv()

# OKX API
OKX_API = os.environ.get("OKX_API", "https://www.okx.com")

# 🔐 OKX API Key
OKX_API_KEY = os.environ.get("OKX_API_KEY", "")
OKX_SECRET_KEY = os.environ.get("OKX_SECRET_KEY", "")
OKX_PASSPHRASE = os.environ.get("OKX_PASSPHRASE", "")

# 交易对（现货）
SYMBOLS = {
    "BTC-USDT": {"name": "BTC/USDT", "min_amount": 0.0001, "price_precision": 1, "amount_precision": 4},
    "ETH-USDT": {"name": "ETH/USDT", "min_amount": 0.001, "price_precision": 1, "amount_precision": 4},
    "SOL-USDT": {"name": "SOL/USDT", "min_amount": 0.01, "price_precision": 1, "amount_precision": 4},
    "DOGE-USDT": {"name": "DOGE/USDT", "min_amount": 1.0, "price_precision": 5, "amount_precision": 0},
}

# ─── 双向网格配置（永续合约 USDT-M） ───────────────────────
GRID = {
    "symbol": "DOGE-USDT",
    "grid_count": 8,              # total grids (4 long + 4 short)
    "price_range_pct": 0.025,      # +/-2.5% range (0.625% per grid)
    "amount_per_grid": 30,         # per grid (DOGE)
    "check_interval": 2,           # tick 间隔（秒），从3缩到2
    "side": "dual",                # "long" | "short" | "dual"
    "take_profit_grids": 2,        # +N 格止盈
}

# 手续费（永续合约 USDT-M，maker 挂单费率）
# 参考 OKX: https://www.okx.com/cn/fees
FEES = {
    "maker": 0.0002,   # 0.02%（限价单挂单，对方吃）
    "taker": 0.0005,   # 0.05%（市价单立即成交）
    "default": 0.0002, # 网格交易用限价单，适用 maker 费率
}

# 风控
RISK = {
    "max_position_usd": 10000,
    "max_loss_pct": 5.0,
    "max_daily_trades": 10000,
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(BASE_DIR, "logs")
BACKTEST_DIR = os.path.join(BASE_DIR, "backtest")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(BACKTEST_DIR, exist_ok=True)

# 模拟交易模式（默认开启）
PAPER_TRADING = False
