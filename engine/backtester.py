"""Historical backtest engine replaying strategies over cached K-line windows."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from config.settings import DB_PATH, KLINE_INTERVAL, KLINE_LIMIT
from database.models import CREATE_MARKET_KLINE
from strategies.base_strategy import BaseStrategy
from utils.logger import get_logger

logger = get_logger(__name__)

_TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _interval_ms(timeframe: str) -> int:
    if timeframe not in _TIMEFRAME_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return _TIMEFRAME_MS[timeframe]


def _symbol_to_binance(symbol: str) -> str:
    return symbol.replace("/", "").upper()


@dataclass
class BacktestTrade:
    open_time: int
    side: str
    price: float
    quantity: float
    fee: float
    equity_after: float


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    start_time: int
    end_time: int
    initial_capital: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    total_trades: int
    win_trades: int
    loss_trades: int
    trades: list[BacktestTrade] = field(default_factory=list)

    def print_summary(self) -> None:
        win_rate = (self.win_trades / self.total_trades * 100) if self.total_trades else 0.0
        print("\n" + "=" * 60)
        print("BACKTEST SUMMARY")
        print("=" * 60)
        print(f"Symbol:          {self.symbol}")
        print(f"Timeframe:       {self.timeframe}")
        print(f"Period:          {self.start_time} -> {self.end_time}")
        print(f"Initial Capital: {self.initial_capital:,.2f} USDT")
        print(f"Final Equity:    {self.final_equity:,.2f} USDT")
        print(f"Total Return:    {self.total_return_pct:+.2f}%")
        print(f"Max Drawdown:    {self.max_drawdown_pct:.2f}%")
        print(f"Total Trades:    {self.total_trades}")
        print(f"Win / Loss:      {self.win_trades} / {self.loss_trades}")
        print(f"Win Rate:        {win_rate:.1f}%")
        print("=" * 60)
        if self.trades:
            print("\nRecent trades (last 10):")
            for t in self.trades[-10:]:
                print(
                    f"  {t.open_time} | {t.side:4s} | "
                    f"price={t.price:.2f} qty={t.quantity} "
                    f"fee={t.fee:.4f} equity={t.equity_after:.2f}"
                )


def fetch_historical_ohlcv(
    symbol: str,
    timeframe: str,
    days: int,
    warmup_bars: int = KLINE_LIMIT,
) -> list[list[float]]:
    """Download OHLCV via Binance public /api/v3/klines (no API key, no load_markets)."""
    bar_ms = _interval_ms(timeframe)
    total_bars = int(days * 24 * 3600 * 1000 / bar_ms) + warmup_bars
    since = int(time.time() * 1000) - total_bars * bar_ms
    pair = _symbol_to_binance(symbol)

    rows: list[list[float]] = []
    seen: set[int] = set()

    while since < int(time.time() * 1000):
        params = urllib.parse.urlencode(
            {
                "symbol": pair,
                "interval": timeframe,
                "startTime": since,
                "limit": 1000,
            }
        )
        url = f"https://api.binance.com/api/v3/klines?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "quant-backtest/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                batch = json.loads(resp.read().decode())
        except Exception as exc:
            logger.error("Failed to download klines from Binance: %s", exc)
            if rows:
                logger.warning("Using %s partially downloaded bars", len(rows))
                break
            raise

        if not batch:
            break

        for item in batch:
            ts = int(item[0])
            if ts in seen:
                continue
            seen.add(ts)
            rows.append(
                [
                    float(ts),
                    float(item[1]),
                    float(item[2]),
                    float(item[3]),
                    float(item[4]),
                    float(item[5]),
                ]
            )

        since = int(batch[-1][0]) + bar_ms
        if len(batch) < 1000:
            break

    rows.sort(key=lambda r: r[0])
    logger.info("Fetched %s bars for %s %s (~%s days)", len(rows), symbol, timeframe, days)
    return rows


def load_ohlcv_from_db(
    db_path: Path,
    symbol: str,
    interval: str = KLINE_INTERVAL,
) -> list[list[float]]:
    """Load cached K-lines from local SQLite (e.g. data/quant.db)."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        """
        SELECT open_time, open, high, low, close, volume
        FROM market_kline
        WHERE symbol = ? AND interval = ?
        ORDER BY open_time ASC
        """,
        (symbol, interval),
    )
    rows = [[float(v) for v in row] for row in cur.fetchall()]
    conn.close()
    logger.info("Loaded %s bars from %s for %s", len(rows), db_path, symbol)
    return rows


def _init_kline_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_MARKET_KLINE)
    conn.commit()
    conn.close()


def _insert_klines(
    db_path: Path,
    symbol: str,
    interval: str,
    rows: list[list[float]],
) -> None:
    if not rows:
        return
    conn = sqlite3.connect(db_path)
    conn.executemany(
        """
        INSERT INTO market_kline (
            symbol, interval, open_time, open, high, low, close, volume
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume
        """,
        [
            (symbol, interval, int(ts), open_, high, low, close, vol)
            for ts, open_, high, low, close, vol in rows
        ],
    )
    conn.commit()
    conn.close()


class BacktestEngine:
    """Replay K-lines through a strategy and simulate spot market fills."""

    def __init__(
        self,
        strategy: BaseStrategy,
        *,
        symbol: str,
        timeframe: str = KLINE_INTERVAL,
        initial_capital: float = 10_000.0,
        commission_rate: float = 0.001,
        warmup_bars: int = KLINE_LIMIT,
    ) -> None:
        self.strategy = strategy
        self.symbol = symbol
        self.timeframe = timeframe
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.warmup_bars = warmup_bars

        self.cash = initial_capital
        self.position_qty = 0.0
        self.entry_price: Optional[float] = None
        self.trades: list[BacktestTrade] = []
        self.equity_curve: list[float] = []
        self._peak_equity = initial_capital

    def _equity(self, mark_price: float) -> float:
        return self.cash + self.position_qty * mark_price

    def _simulate_fill(self, signal: dict[str, Any], bar: dict[str, Any]) -> dict[str, Any]:
        side = signal["side"]
        qty = float(signal["quantity"])
        price = float(bar["close"])
        notional = price * qty
        fee = notional * self.commission_rate

        if side == "buy":
            cost = notional + fee
            if cost > self.cash:
                logger.debug("Skip buy: insufficient cash (need %.2f, have %.2f)", cost, self.cash)
                return {"status": "rejected", "side": side, "price": price}
            self.cash -= cost
            self.position_qty += qty
            self.entry_price = price
        elif side == "sell":
            if self.position_qty < qty - 1e-12:
                logger.debug("Skip sell: no position")
                return {"status": "rejected", "side": side, "price": price}
            proceeds = notional - fee
            self.cash += proceeds
            self.position_qty -= qty
            if self.position_qty <= 1e-12:
                self.position_qty = 0.0
                self.entry_price = None
        else:
            return {"status": "rejected", "side": side, "price": price}

        equity = self._equity(price)
        self.trades.append(
            BacktestTrade(
                open_time=int(bar["open_time"]),
                side=side,
                price=price,
                quantity=qty,
                fee=fee,
                equity_after=equity,
            )
        )
        return {
            "status": "FILLED",
            "side": side,
            "price": price,
            "quantity": qty,
            "filled_quantity": qty,
            "client_order_id": signal.get("client_order_id"),
        }

    def _update_drawdown(self, equity: float) -> float:
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity <= 0:
            return 0.0
        dd = (self._peak_equity - equity) / self._peak_equity * 100
        return dd

    async def run(self, ohlcv_rows: list[list[float]]) -> BacktestResult:
        if len(ohlcv_rows) <= self.warmup_bars:
            raise ValueError(
                f"Need more than {self.warmup_bars} bars for indicator warmup, got {len(ohlcv_rows)}"
            )

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "backtest.db"
            _init_kline_db(db_path)
            # Seed indicator warmup only; append one bar per step (no future leak).
            _insert_klines(
                db_path,
                self.symbol,
                self.timeframe,
                ohlcv_rows[: self.warmup_bars],
            )
            self.strategy.params["db_path"] = str(db_path)
            if hasattr(self.strategy, "db_path"):
                self.strategy.db_path = db_path

            await self.strategy.start()

            max_drawdown = 0.0
            win_trades = 0
            loss_trades = 0
            last_buy_price: Optional[float] = None

            for row in ohlcv_rows[self.warmup_bars :]:
                _insert_klines(db_path, self.symbol, self.timeframe, [row])

                ts, open_, high, low, close, volume = row
                bar = {
                    "symbol": self.symbol,
                    "interval": self.timeframe,
                    "open_time": int(ts),
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }

                signals = await self.strategy.on_kline_update(bar)
                for signal in signals:
                    if signal.get("side") == "buy":
                        last_buy_price = float(bar["close"])
                    fill = self._simulate_fill(signal, bar)
                    await self.strategy.on_order_status(fill)
                    if fill.get("status") == "FILLED" and fill.get("side") == "sell" and last_buy_price:
                        pnl = (float(fill["price"]) - last_buy_price) * float(fill["quantity"])
                        if pnl >= 0:
                            win_trades += 1
                        else:
                            loss_trades += 1
                        last_buy_price = None

                equity = self._equity(float(close))
                self.equity_curve.append(equity)
                max_drawdown = max(max_drawdown, self._update_drawdown(equity))

            await self.strategy.stop()

            final_price = float(ohlcv_rows[-1][4])
            final_equity = self._equity(final_price)
            total_return = (final_equity - self.initial_capital) / self.initial_capital * 100

            return BacktestResult(
                symbol=self.symbol,
                timeframe=self.timeframe,
                start_time=int(ohlcv_rows[self.warmup_bars][0]),
                end_time=int(ohlcv_rows[-1][0]),
                initial_capital=self.initial_capital,
                final_equity=final_equity,
                total_return_pct=total_return,
                max_drawdown_pct=max_drawdown,
                total_trades=len(self.trades),
                win_trades=win_trades,
                loss_trades=loss_trades,
                trades=self.trades,
            )


async def run_backtest(
    strategy: BaseStrategy,
    *,
    symbol: str,
    timeframe: str = KLINE_INTERVAL,
    days: int = 7,
    initial_capital: float = 10_000.0,
    commission_rate: float = 0.001,
    ohlcv_rows: Optional[list[list[float]]] = None,
    from_db: Optional[Path] = None,
) -> BacktestResult:
    if ohlcv_rows is not None:
        rows = ohlcv_rows
    elif from_db is not None:
        rows = load_ohlcv_from_db(from_db, symbol, timeframe)
    else:
        rows = fetch_historical_ohlcv(symbol, timeframe, days)

    if len(rows) <= KLINE_LIMIT:
        raise ValueError(
            f"Need more than {KLINE_LIMIT} bars for indicator warmup, got {len(rows)}. "
            "Use a longer --days range, run the live engine to cache klines, or pass --from-db."
        )

    engine = BacktestEngine(
        strategy,
        symbol=symbol,
        timeframe=timeframe,
        initial_capital=initial_capital,
        commission_rate=commission_rate,
    )
    return await engine.run(rows)
