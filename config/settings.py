"""Global trading parameters and path configuration."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
DB_PATH = DATA_DIR / "quant.db"

SYMBOL_WHITELIST: tuple[str, ...] = tuple(
    s.strip()
    for s in os.getenv("SYMBOL_WHITELIST", "BTC/USDT,ETH/USDT").split(",")
    if s.strip()
)

TICK_INTERVAL_SEC = float(os.getenv("TICK_INTERVAL_SEC", "5"))
KLINE_INTERVAL = os.getenv("KLINE_INTERVAL", "1m")
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "100"))

BINANCE_SANDBOX = os.getenv("BINANCE_SANDBOX", "false").lower() in {"1", "true", "yes"}

MAX_LEVERAGE = int(os.getenv("MAX_LEVERAGE", "10"))
MAX_DAILY_DRAWDOWN_PCT = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "5.0"))
MAX_ORDER_NOTIONAL_USD = float(os.getenv("MAX_ORDER_NOTIONAL_USD", "10000"))
MAX_ORDERS_PER_MINUTE = int(os.getenv("MAX_ORDERS_PER_MINUTE", "30"))

WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
