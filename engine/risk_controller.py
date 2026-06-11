"""Pre-trade risk checks, drawdown circuit breaker, and order rate limiting."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class RiskViolation(Exception):
    """Raised when a trading signal fails a risk rule."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass
class RiskConfig:
    max_leverage: int = 10
    max_daily_drawdown_pct: float = 5.0
    max_order_notional_usd: float = 10_000.0
    max_orders_per_minute: int = 30
    symbol_whitelist: tuple[str, ...] = ()


@dataclass
class RiskController:
    """Intercept outgoing signals before they reach the exchange gateway."""

    config: RiskConfig
    _order_timestamps: deque[float] = field(default_factory=deque, init=False)
    _day_start_equity: Optional[float] = field(default=None, init=False)
    _current_equity: Optional[float] = field(default=None, init=False)
    _circuit_breaker_tripped: bool = field(default=False, init=False)
    _breaker_alert_pending: bool = field(default=False, init=False)
    _last_breaker_reason: Optional[str] = field(default=None, init=False)

    @property
    def circuit_breaker_tripped(self) -> bool:
        return self._circuit_breaker_tripped

    def update_equity(self, equity: float) -> None:
        if self._day_start_equity is None:
            self._day_start_equity = equity
        self._current_equity = equity
        self._evaluate_drawdown()

    def trip_circuit_breaker(self, reason: str) -> None:
        self._circuit_breaker_tripped = True
        self._last_breaker_reason = reason
        self._breaker_alert_pending = True
        logger.error("Circuit breaker tripped: %s", reason)

    def consume_breaker_alert(self) -> Optional[str]:
        if not self._breaker_alert_pending:
            return None
        self._breaker_alert_pending = False
        return self._last_breaker_reason

    def reset_circuit_breaker(self) -> None:
        self._circuit_breaker_tripped = False
        self._day_start_equity = self._current_equity
        logger.warning("Circuit breaker reset; daily drawdown baseline refreshed")

    def _evaluate_drawdown(self) -> None:
        if self._circuit_breaker_tripped:
            return
        if self._day_start_equity is None or self._current_equity is None:
            return
        if self._day_start_equity <= 0:
            return

        drawdown_pct = (
            (self._day_start_equity - self._current_equity) / self._day_start_equity
        ) * 100
        if drawdown_pct >= self.config.max_daily_drawdown_pct:
            self.trip_circuit_breaker(
                f"daily drawdown {drawdown_pct:.2f}% exceeds limit "
                f"{self.config.max_daily_drawdown_pct:.2f}%"
            )

    def _record_order_attempt(self) -> None:
        now = time.monotonic()
        self._order_timestamps.append(now)
        cutoff = now - 60.0
        while self._order_timestamps and self._order_timestamps[0] < cutoff:
            self._order_timestamps.popleft()

    def _check_order_rate(self) -> None:
        if len(self._order_timestamps) >= self.config.max_orders_per_minute:
            raise RiskViolation(
                "order_rate_limit",
                f"Order rate exceeded: {self.config.max_orders_per_minute}/minute",
            )

    def _check_symbol(self, symbol: str) -> None:
        if self.config.symbol_whitelist and symbol not in self.config.symbol_whitelist:
            raise RiskViolation(
                "symbol_not_whitelisted",
                f"Symbol {symbol} is not in whitelist",
            )

    def _check_leverage(self, signal: dict[str, Any]) -> None:
        leverage = signal.get("leverage")
        if leverage is None:
            return
        if leverage > self.config.max_leverage:
            raise RiskViolation(
                "max_leverage_exceeded",
                f"Leverage {leverage} exceeds max {self.config.max_leverage}",
            )

    def _estimate_notional(self, signal: dict[str, Any]) -> float:
        notional = signal.get("notional_usd")
        if notional is not None:
            return float(notional)

        quantity = float(signal.get("quantity", 0))
        price = signal.get("price")
        if price is not None:
            return quantity * float(price)
        return 0.0

    def _check_notional(self, signal: dict[str, Any]) -> None:
        notional = self._estimate_notional(signal)
        if notional <= 0:
            return
        if notional > self.config.max_order_notional_usd:
            raise RiskViolation(
                "max_notional_exceeded",
                f"Order notional ${notional:.2f} exceeds max "
                f"${self.config.max_order_notional_usd:.2f}",
            )

    def validate_signal(self, signal: dict[str, Any]) -> None:
        """Run all pre-trade checks. Raises RiskViolation when blocked."""
        action = signal.get("action", "create")
        if action == "cancel":
            return

        if self._circuit_breaker_tripped:
            raise RiskViolation(
                "circuit_breaker",
                "Trading halted by daily drawdown circuit breaker",
            )

        symbol = signal.get("symbol")
        if not symbol:
            raise RiskViolation("missing_symbol", "Signal missing symbol")

        self._check_symbol(symbol)
        self._check_leverage(signal)
        self._check_notional(signal)
        self._check_order_rate()

        logger.debug("Risk check passed for signal: %s", signal.get("client_order_id") or symbol)

    def approve_and_record(self, signal: dict[str, Any]) -> None:
        """Validate signal and record an order attempt for rate limiting."""
        self.validate_signal(signal)
        if signal.get("action", "create") != "cancel":
            self._record_order_attempt()

    def get_status(self) -> dict[str, Any]:
        drawdown_pct: Optional[float] = None
        if (
            self._day_start_equity is not None
            and self._current_equity is not None
            and self._day_start_equity > 0
        ):
            drawdown_pct = (
                (self._day_start_equity - self._current_equity) / self._day_start_equity
            ) * 100

        return {
            "circuit_breaker_tripped": self._circuit_breaker_tripped,
            "day_start_equity": self._day_start_equity,
            "current_equity": self._current_equity,
            "drawdown_pct": drawdown_pct,
            "orders_last_minute": len(self._order_timestamps),
            "max_leverage": self.config.max_leverage,
            "max_daily_drawdown_pct": self.config.max_daily_drawdown_pct,
            "max_order_notional_usd": self.config.max_order_notional_usd,
            "max_orders_per_minute": self.config.max_orders_per_minute,
            "symbol_whitelist": list(self.config.symbol_whitelist),
        }
