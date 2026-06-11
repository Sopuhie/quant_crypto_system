"""SQLite table definitions (DDL) and row dataclass mappings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

TABLE_STRATEGY_CONFIG = "strategy_config"
TABLE_ACCOUNT_POSITION = "account_position"
TABLE_TRADE_ORDERS = "trade_orders"
TABLE_MARKET_KLINE = "market_kline"

ALL_TABLES = (
    TABLE_STRATEGY_CONFIG,
    TABLE_ACCOUNT_POSITION,
    TABLE_TRADE_ORDERS,
    TABLE_MARKET_KLINE,
)

CREATE_STRATEGY_CONFIG = """
CREATE TABLE IF NOT EXISTS strategy_config (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name   TEXT    NOT NULL UNIQUE,
    symbol          TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'paused',
    params_json     TEXT    NOT NULL DEFAULT '{}',
    target_profit   REAL,
    target_loss     REAL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_ACCOUNT_POSITION = """
CREATE TABLE IF NOT EXISTS account_position (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_type    TEXT    NOT NULL,
    asset           TEXT    NOT NULL,
    symbol          TEXT    NOT NULL DEFAULT '',
    free            REAL    NOT NULL DEFAULT 0,
    locked          REAL    NOT NULL DEFAULT 0,
    total           REAL    NOT NULL DEFAULT 0,
    position_side   TEXT    NOT NULL DEFAULT 'none',
    entry_price     REAL,
    unrealized_pnl  REAL,
    leverage        INTEGER,
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (account_type, asset, symbol, position_side)
);
"""

CREATE_TRADE_ORDERS = """
CREATE TABLE IF NOT EXISTS trade_orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name       TEXT    NOT NULL,
    client_order_id     TEXT    NOT NULL UNIQUE,
    exchange_order_id   TEXT,
    symbol              TEXT    NOT NULL,
    side                TEXT    NOT NULL,
    order_type          TEXT    NOT NULL,
    price               REAL,
    quantity            REAL    NOT NULL,
    filled_quantity     REAL    NOT NULL DEFAULT 0,
    status              TEXT    NOT NULL DEFAULT 'pending',
    signal_json         TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_MARKET_KLINE = """
CREATE TABLE IF NOT EXISTS market_kline (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    interval    TEXT    NOT NULL,
    open_time   INTEGER NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    close_time  INTEGER,
    UNIQUE (symbol, interval, open_time)
);
"""

CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_strategy_config_status ON strategy_config (status);",
    "CREATE INDEX IF NOT EXISTS idx_account_position_symbol ON account_position (symbol);",
    "CREATE INDEX IF NOT EXISTS idx_trade_orders_strategy ON trade_orders (strategy_name);",
    "CREATE INDEX IF NOT EXISTS idx_trade_orders_status ON trade_orders (status);",
    "CREATE INDEX IF NOT EXISTS idx_market_kline_lookup ON market_kline (symbol, interval, open_time);",
)

DDL_STATEMENTS = (
    CREATE_STRATEGY_CONFIG,
    CREATE_ACCOUNT_POSITION,
    CREATE_TRADE_ORDERS,
    CREATE_MARKET_KLINE,
    *CREATE_INDEXES,
)


@dataclass
class StrategyConfig:
    strategy_name: str
    symbol: str
    status: str = "paused"
    params_json: str = "{}"
    target_profit: Optional[float] = None
    target_loss: Optional[float] = None
    id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class AccountPosition:
    account_type: str
    asset: str
    symbol: str = ""
    free: float = 0.0
    locked: float = 0.0
    total: float = 0.0
    position_side: str = "none"
    entry_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    leverage: Optional[int] = None
    id: Optional[int] = None
    updated_at: Optional[str] = None


@dataclass
class TradeOrder:
    strategy_name: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: Optional[float] = None
    filled_quantity: float = 0.0
    status: str = "pending"
    exchange_order_id: Optional[str] = None
    signal_json: Optional[str] = None
    id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class MarketKline:
    symbol: str
    interval: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: Optional[int] = None
    id: Optional[int] = None
