"""System bootstrapper and main asynchronous trading loop with live WebSocket stream."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from typing import Any, Optional

import ccxt.pro as ccxtpro

from config.settings import (
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
    load_binance_credentials,
    load_binance_sandbox,
)
from database.connection import DatabaseConnection
from engine.risk_controller import RiskConfig, RiskController, RiskViolation
from gateway.binance_client import AuthenticationError, BinanceClient
from gateway.order_executor import OrderExecutor
from strategies.base_strategy import BaseStrategy
from strategies.ma_trend_strategy import MaTrendStrategy
from utils.logger import get_logger, setup_logging
from utils.notifier import SystemNotifier

logger = get_logger(__name__)


class QuantTradingSystem:
    """Wire database, gateway, risk control, and strategies into one async loop."""

    def __init__(
        self,
        strategies: list[BaseStrategy],
        *,
        sandbox: Optional[bool] = None,
        tick_interval: float = TICK_INTERVAL_SEC,
    ) -> None:
        self.strategies = strategies
        self.sandbox = load_binance_sandbox() if sandbox is None else sandbox
        self.tick_interval = tick_interval
        self._running = False
        self._active = False
        self._shutdown_event = asyncio.Event()
        self._ws_tasks: list[asyncio.Task[None]] = []

        self.db = DatabaseConnection(DB_PATH)
        api_key, api_secret = load_binance_credentials()
        self.client = BinanceClient(api_key, api_secret, sandbox=self.sandbox)
        self.executor = OrderExecutor(self.client)
        self.ws_client = self._build_ws_client()
        self.notifier = SystemNotifier()
        self.risk = RiskController(
            RiskConfig(
                max_leverage=MAX_LEVERAGE,
                max_daily_drawdown_pct=MAX_DAILY_DRAWDOWN_PCT,
                max_order_notional_usd=MAX_ORDER_NOTIONAL_USD,
                max_orders_per_minute=MAX_ORDERS_PER_MINUTE,
                symbol_whitelist=SYMBOL_WHITELIST,
            )
        )

    def _build_ws_client(self) -> ccxtpro.binance:
        api_key, api_secret = load_binance_credentials()
        config: dict[str, Any] = {
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
        if api_key and api_secret:
            config["apiKey"] = api_key
            config["secret"] = api_secret

        exchange = ccxtpro.binance(config)
        if self.sandbox and api_key and api_secret:
            exchange.set_sandbox_mode(True)
        return exchange

    async def startup(self) -> None:
        logger.info("Starting quant trading system (sandbox=%s)", self.sandbox)
        self.db.initialize_schema()

        await asyncio.to_thread(self.client.initialize)

        # Sync historical bars to ensure indicator vectors are full on boot
        logger.info("Syncing historical kline buffers for initialized strategies...")
        for strategy in self.strategies:
            try:
                # Query historical matrix up to the config limit
                rows = await asyncio.to_thread(
                    self.client.spot.fetch_ohlcv,
                    strategy.symbol,
                    KLINE_INTERVAL,
                    None,
                    KLINE_LIMIT,
                )
                if rows:
                    synced_count = 0
                    for row in rows:
                        ts, open_, high, low, close, volume = row
                        kline_data = {
                            "symbol": strategy.symbol,
                            "interval": KLINE_INTERVAL,
                            "open_time": ts,
                            "open": open_,
                            "high": high,
                            "low": low,
                            "close": close,
                            "volume": volume,
                        }
                        await self._persist_kline(kline_data)
                        synced_count += 1
                    logger.info(
                        "Successfully synced %s historical bars for %s",
                        synced_count,
                        strategy.symbol,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to warm up historical database buffer for %s: %s",
                    strategy.symbol,
                    exc,
                )

        await self._refresh_equity()
        for strategy in self.strategies:
            await strategy.start()

        self._running = True
        self._active = True
        logger.info(
            "System ready: strategies=%s symbols=%s tick=%ss mode=%s",
            len(self.strategies),
            SYMBOL_WHITELIST,
            self.tick_interval,
            "public-data-only" if self.client.public_only else "full-trading",
        )

        for strategy in self.strategies:
            task = asyncio.create_task(
                self._websocket_market_stream(strategy),
                name=f"ws-{strategy.symbol}",
            )
            self._ws_tasks.append(task)

    async def _websocket_market_stream(self, strategy: BaseStrategy) -> None:
        """Consume live candles via CCXT Pro WebSocket and drive strategy evaluation."""
        logger.info("WebSocket stream activated for symbol %s", strategy.symbol)
        while self._running:
            try:
                ohlcv = await self.ws_client.watch_ohlcv(strategy.symbol, KLINE_INTERVAL)
                if not ohlcv:
                    continue

                ts, open_, high, low, close, volume = ohlcv[-1]
                kline = {
                    "symbol": strategy.symbol,
                    "interval": KLINE_INTERVAL,
                    "open_time": ts,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }

                await self._persist_kline(kline)

                if self.risk.circuit_breaker_tripped:
                    logger.error("Circuit breaker active; skipping strategy execution")
                    continue

                kline_signals = await strategy.on_kline_update(kline)
                if kline_signals:
                    await self._dispatch_signals(strategy, kline_signals)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("WebSocket stream error on %s: %s", strategy.symbol, exc)
                await asyncio.sleep(5)

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

        for task in self._ws_tasks:
            task.cancel()
        if self._ws_tasks:
            await asyncio.gather(*self._ws_tasks, return_exceptions=True)
        await self.ws_client.close()

        for strategy in self.strategies:
            await strategy.stop()

        await asyncio.to_thread(self.client.close)
        self.db.close()
        self._shutdown_event.set()

    async def _refresh_equity(self) -> None:
        if not self.client.has_credentials:
            return
        try:
            balance = await asyncio.to_thread(self.client.spot.fetch_balance)
            total_usdt = float(balance.get("total", {}).get("USDT", 0) or 0)
            self.risk.update_equity(total_usdt)
            logger.debug("Equity snapshot updated: USDT=%.4f", total_usdt)
        except Exception as exc:
            logger.warning("Unable to refresh equity for risk checks: %s", exc)

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
            if exc.code == "circuit_breaker":
                await self.notifier.send_notification(
                    f"🚨 熔断拦截\n{exc}"
                )
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

        if action != "cancel" and result.get("status") in ("closed", "FILLED"):
            await self.notifier.send_notification(
                f"🟢 订单成交确认\n"
                f"交易对: {symbol}\n"
                f"方向: {signal['side']}\n"
                f"数量: {signal['quantity']}"
            )

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

            if result.get("status") in ("closed", "FILLED"):
                side = result.get("side") or signal.get("side")
                market_type = signal.get("market_type", "spot")
                symbol = result.get("symbol") or signal.get("symbol")
                qty = float(
                    result.get("filled_quantity")
                    or result.get("quantity")
                    or signal.get("quantity")
                    or 0.0
                )
                price = float(result.get("price") or 0.0)
                asset = symbol.split("/")[0]

                if side == "buy":
                    free = qty
                    total = qty
                    entry_price = price
                else:
                    free = 0.0
                    total = 0.0
                    entry_price = None

                conn.execute(
                    """
                    INSERT INTO account_position (
                        account_type, asset, symbol, free, total, position_side, entry_price
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_type, asset, symbol, position_side) DO UPDATE SET
                        free = excluded.free,
                        total = excluded.total,
                        entry_price = excluded.entry_price,
                        updated_at = datetime('now')
                    """,
                    (
                        market_type,
                        asset,
                        symbol,
                        free,
                        total,
                        "long",
                        entry_price,
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
            elif hasattr(strategy, "is_order_pending"):
                await strategy.on_order_status(
                    {
                        "status": "rejected",
                        "side": signal.get("side"),
                        "price": signal.get("price"),
                    }
                )

    async def run(self) -> None:
        await self.startup()
        last_equity_refresh = 0.0
        equity_refresh_cooldown = 30.0

        try:
            while self._running:
                now = asyncio.get_running_loop().time()
                if now - last_equity_refresh >= equity_refresh_cooldown:
                    await self._refresh_equity()
                    last_equity_refresh = now
                    breaker_reason = self.risk.consume_breaker_alert()
                    if breaker_reason:
                        await self.notifier.send_notification(
                            f"🚨 熔断触发\n{breaker_reason}"
                        )

                if self.risk.circuit_breaker_tripped:
                    logger.error("Circuit breaker active; skipping strategy cycle")
                else:
                    for strategy in self.strategies:
                        tick_signals = await strategy.on_tick()
                        if tick_signals:
                            await self._dispatch_signals(strategy, tick_signals)

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
    """Dynamically constructs runtime active strategy objects linked into the main execution array."""
    _ = db
    active_instances: list[BaseStrategy] = []

    # Instantiate MACD/KDJ tracking engine on default symbol configurations
    # Ensure format alignment matches CCXT target symbols (e.g. "BTC/USDT")
    active_instances.append(
        MaTrendStrategy(
            name="BTC_ShortTerm_Trend",
            symbol="BTC/USDT",
            params={
                "market_type": "spot",
                "order_quantity": 0.0005,
                "fast_period": 12,
                "slow_period": 26,
                "signal_period": 9,
            },
        )
    )
    return active_instances


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Binance quantitative trading system")
    parser.add_argument(
        "--sandbox",
        action="store_true",
        default=load_binance_sandbox(),
        help="Use Binance sandbox/testnet (overrides secure_keys.json when passed)",
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
