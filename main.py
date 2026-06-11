"""System bootstrapper and main asynchronous trading loop."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from typing import Any, Optional

from config.settings import (
    BINANCE_SANDBOX,
    DB_PATH,
    KLINE_INTERVAL,
    KLINE_LIMIT,
    LOG_DIR,
    MAX_DAILY_DRAWDOWN_PCT,
    MAX_LEVERAGE,
    MAX_ORDER_NOTIONAL_USD,
    MAX_ORDERS_PER_MINUTE,
    SYMBOL_WHITELIST,
    TICK_INTERVAL_SEC,
)
from database.connection import DatabaseConnection
from engine.risk_controller import RiskConfig, RiskController, RiskViolation
from gateway.binance_client import AuthenticationError, BinanceClient
from gateway.order_executor import OrderExecutor
from strategies.base_strategy import BaseStrategy
from utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


class QuantTradingSystem:
    """Wire database, gateway, risk control, and strategies into one async loop."""

    def __init__(
        self,
        strategies: list[BaseStrategy],
        *,
        sandbox: bool = BINANCE_SANDBOX,
        tick_interval: float = TICK_INTERVAL_SEC,
    ) -> None:
        self.strategies = strategies
        self.sandbox = sandbox
        self.tick_interval = tick_interval
        self._running = False
        self._active = False
        self._shutdown_event = asyncio.Event()

        self.db = DatabaseConnection(DB_PATH)
        self.client = BinanceClient(sandbox=sandbox)
        self.executor = OrderExecutor(self.client)
        self.risk = RiskController(
            RiskConfig(
                max_leverage=MAX_LEVERAGE,
                max_daily_drawdown_pct=MAX_DAILY_DRAWDOWN_PCT,
                max_order_notional_usd=MAX_ORDER_NOTIONAL_USD,
                max_orders_per_minute=MAX_ORDERS_PER_MINUTE,
                symbol_whitelist=SYMBOL_WHITELIST,
            )
        )

    async def startup(self) -> None:
        logger.info("Starting quant trading system (sandbox=%s)", self.sandbox)
        self.db.initialize_schema()

        try:
            await asyncio.to_thread(self.client.initialize)
        except AuthenticationError:
            logger.warning(
                "Binance credentials unavailable; running in offline simulation mode"
            )

        await self._refresh_equity()
        for strategy in self.strategies:
            await strategy.start()

        self._running = True
        self._active = True
        logger.info(
            "System ready: strategies=%s symbols=%s",
            len(self.strategies),
            SYMBOL_WHITELIST,
        )

    @property
    def is_running(self) -> bool:
        return self._running and self._active

    def get_status(self) -> dict[str, Any]:
        risk_status = self.risk.get_status()
        return {
            "running": self.is_running,
            "sandbox": self.sandbox,
            "tick_interval": self.tick_interval,
            "strategy_count": len(self.strategies),
            "strategies": [
                {"name": s.name, "symbol": s.symbol, "running": s.is_running}
                for s in self.strategies
            ],
            "risk": risk_status,
        }

    async def shutdown(self) -> None:
        if not self._active:
            return
        self._running = False
        self._active = False
        logger.info("Shutting down quant trading system")

        for strategy in self.strategies:
            await strategy.stop()

        await asyncio.to_thread(self.client.close)
        self.db.close()
        self._shutdown_event.set()

    async def _refresh_equity(self) -> None:
        try:
            balance = await asyncio.to_thread(self.client.spot.fetch_balance)
            total_usdt = float(balance.get("total", {}).get("USDT", 0) or 0)
            self.risk.update_equity(total_usdt)
            logger.debug("Equity snapshot updated: USDT=%.4f", total_usdt)
        except Exception as exc:
            logger.warning("Unable to refresh equity for risk checks: %s", exc)

    async def _fetch_latest_kline(self, symbol: str) -> Optional[dict[str, Any]]:
        try:
            rows = await asyncio.to_thread(
                self.client.spot.fetch_ohlcv,
                symbol,
                KLINE_INTERVAL,
                None,
                1,
            )
        except Exception as exc:
            logger.warning("Failed to fetch kline for %s: %s", symbol, exc)
            return None

        if not rows:
            return None

        ts, open_, high, low, close, volume = rows[-1]
        return {
            "symbol": symbol,
            "interval": KLINE_INTERVAL,
            "open_time": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    async def _persist_kline(self, kline: dict[str, Any]) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO market_kline (
                    symbol, interval, open_time, open, high, low, close, volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume
                """,
                (
                    kline["symbol"],
                    kline["interval"],
                    kline["open_time"],
                    kline["open"],
                    kline["high"],
                    kline["low"],
                    kline["close"],
                    kline["volume"],
                ),
            )

    async def _execute_signal(self, signal: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            self.risk.approve_and_record(signal)
        except RiskViolation as exc:
            logger.warning("Signal blocked by risk controller [%s]: %s", exc.code, exc)
            return None

        action = signal.get("action", "create")
        market_type = signal.get("market_type", "spot")
        symbol = signal["symbol"]

        try:
            if action == "cancel":
                result = await asyncio.to_thread(
                    self.executor.cancel_order,
                    market_type,
                    symbol,
                    order_id=signal.get("exchange_order_id"),
                    client_order_id=signal.get("client_order_id"),
                )
            else:
                result = await asyncio.to_thread(
                    self.executor.create_order,
                    market_type,
                    symbol,
                    signal["side"],
                    signal["order_type"],
                    float(signal["quantity"]),
                    price=signal.get("price"),
                    client_order_id=signal.get("client_order_id"),
                    params=signal.get("params"),
                )
        except Exception as exc:
            logger.error("Order execution failed: %s", exc)
            return None

        await self._persist_trade_order(signal, result)
        return result

    async def _persist_trade_order(
        self,
        signal: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO trade_orders (
                    strategy_name, client_order_id, exchange_order_id, symbol,
                    side, order_type, price, quantity, filled_quantity, status, signal_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    exchange_order_id=excluded.exchange_order_id,
                    filled_quantity=excluded.filled_quantity,
                    status=excluded.status,
                    updated_at=datetime('now')
                """,
                (
                    signal.get("strategy_name", "unknown"),
                    result.get("client_order_id") or signal.get("client_order_id"),
                    result.get("exchange_order_id"),
                    result.get("symbol") or signal.get("symbol"),
                    result.get("side") or signal.get("side"),
                    result.get("order_type") or signal.get("order_type"),
                    result.get("price"),
                    result.get("quantity") or signal.get("quantity"),
                    result.get("filled_quantity") or 0,
                    result.get("status"),
                    json.dumps(signal, ensure_ascii=False),
                ),
            )

    async def _dispatch_signals(
        self,
        strategy: BaseStrategy,
        signals: list[dict[str, Any]],
    ) -> None:
        for signal in signals:
            result = await self._execute_signal(signal)
            if result is not None:
                await strategy.on_order_status(result)

    async def _run_strategy_cycle(self, strategy: BaseStrategy) -> None:
        if not strategy.is_running:
            return

        tick_signals = await strategy.on_tick()
        if tick_signals:
            await self._dispatch_signals(strategy, tick_signals)

        kline = await self._fetch_latest_kline(strategy.symbol)
        if kline is None:
            return

        await self._persist_kline(kline)
        kline_signals = await strategy.on_kline_update(kline)
        if kline_signals:
            await self._dispatch_signals(strategy, kline_signals)

    async def run(self) -> None:
        await self.startup()
        try:
            while self._running:
                await self._refresh_equity()

                if self.risk.circuit_breaker_tripped:
                    logger.error("Circuit breaker active; skipping strategy cycle")
                else:
                    for strategy in self.strategies:
                        await self._run_strategy_cycle(strategy)

                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.tick_interval,
                    )
                    break
                except asyncio.TimeoutError:
                    continue
        finally:
            await self.shutdown()

    def request_shutdown(self) -> None:
        self._running = False
        self._shutdown_event.set()


def _load_strategies_from_db(db: DatabaseConnection) -> list[BaseStrategy]:
    """Placeholder for dynamic strategy loading; returns empty until implementations exist."""
    _ = db
    return []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Binance quantitative trading system")
    parser.add_argument(
        "--sandbox",
        action="store_true",
        default=BINANCE_SANDBOX,
        help="Use Binance sandbox/testnet",
    )
    parser.add_argument(
        "--tick-interval",
        type=float,
        default=TICK_INTERVAL_SEC,
        help="Main loop interval in seconds",
    )
    return parser.parse_args()


async def async_main(strategies: Optional[list[BaseStrategy]] = None) -> None:
    setup_logging(log_dir=LOG_DIR)
    args = parse_args()

    db = DatabaseConnection(DB_PATH)
    db.initialize_schema()
    active_strategies = strategies if strategies is not None else _load_strategies_from_db(db)

    system = QuantTradingSystem(
        active_strategies,
        sandbox=args.sandbox,
        tick_interval=args.tick_interval,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, system.request_shutdown)
        except NotImplementedError:
            # Windows does not support add_signal_handler for SIGTERM in all contexts.
            pass

    await system.run()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
