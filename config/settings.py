"""Global trading parameters and path configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
DB_PATH = DATA_DIR / "quant.db"
SECURE_KEYS_PATH = PROJECT_ROOT / "config" / "secure_keys.json"

SYMBOL_WHITELIST: tuple[str, ...] = tuple(
    s.strip()
    for s in os.getenv("SYMBOL_WHITELIST", "BTC/USDT,ETH/USDT").split(",")
    if s.strip()
)

TICK_INTERVAL_SEC = float(os.getenv("TICK_INTERVAL_SEC", "5"))
KLINE_INTERVAL = os.getenv("KLINE_INTERVAL", "1m")
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "100"))

MAX_LEVERAGE = int(os.getenv("MAX_LEVERAGE", "10"))
MAX_DAILY_DRAWDOWN_PCT = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "5.0"))
MAX_ORDER_NOTIONAL_USD = float(os.getenv("MAX_ORDER_NOTIONAL_USD", "10000"))
MAX_ORDERS_PER_MINUTE = int(os.getenv("MAX_ORDERS_PER_MINUTE", "30"))

WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))

_PLACEHOLDER_MARKERS = (
    "your_binance",
    "在此填入",
    "here",
    "placeholder",
)


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return not lowered or any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def load_secure_config() -> dict[str, Any]:
    """Load config/secure_keys.json; returns empty dict when missing or invalid."""
    if not SECURE_KEYS_PATH.exists():
        return {}

    try:
        data = json.loads(SECURE_KEYS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_binance_credentials() -> tuple[Optional[str], Optional[str]]:
    """
    Load Binance API credentials.

    Priority:
    1. config/secure_keys.json
    2. Environment variables BINANCE_API_KEY / BINANCE_API_SECRET
    """
    data = load_secure_config()
    api_key = data.get("BINANCE_API_KEY") or data.get("api_key")
    api_secret = data.get("BINANCE_API_SECRET") or data.get("api_secret")

    api_key = os.getenv("BINANCE_API_KEY") or api_key
    api_secret = os.getenv("BINANCE_API_SECRET") or api_secret

    if api_key and not _is_placeholder(str(api_key)):
        api_key = str(api_key).strip()
    else:
        api_key = None

    if api_secret and not _is_placeholder(str(api_secret)):
        api_secret = str(api_secret).strip()
    else:
        api_secret = None

    return api_key, api_secret


def load_binance_sandbox(default: bool = True) -> bool:
    """
    Load sandbox flag.

    Priority:
    1. config/secure_keys.json (BINANCE_SANDBOX or sandbox)
    2. Environment variable BINANCE_SANDBOX
    3. default argument
    """
    data = load_secure_config()
    if "BINANCE_SANDBOX" in data:
        return bool(data["BINANCE_SANDBOX"])
    if "sandbox" in data:
        return bool(data["sandbox"])

    env_value = os.getenv("BINANCE_SANDBOX")
    if env_value is not None:
        return env_value.lower() in {"1", "true", "yes"}

    return default


# Backward-compatible module constant
BINANCE_SANDBOX = load_binance_sandbox(default=True)
