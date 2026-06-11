"""Abstract base class for quantitative trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class BaseStrategy(ABC):
    """Standard strategy lifecycle and event handlers."""

    def __init__(
        self,
        name: str,
        symbol: str,
        params: Optional[dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.symbol = symbol
        self.params = params or {}
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        logger.info("Strategy started: %s (%s)", self.name, self.symbol)

    async def stop(self) -> None:
        self._running = False
        logger.info("Strategy stopped: %s (%s)", self.name, self.symbol)

    @abstractmethod
    async def on_kline_update(self, kline: dict[str, Any]) -> list[dict[str, Any]]:
        """Handle a new or updated Kline bar; return zero or more order signals."""

    @abstractmethod
    async def on_order_status(self, order: dict[str, Any]) -> None:
        """React to order lifecycle updates from the gateway."""

    @abstractmethod
    async def on_tick(self) -> list[dict[str, Any]]:
        """Periodic hook for time-driven logic; return zero or more order signals."""

    def build_signal(
        self,
        *,
        action: str = "create",
        market_type: str = "spot",
        side: str = "buy",
        order_type: str = "limit",
        quantity: float = 0.0,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
        leverage: Optional[int] = None,
        notional_usd: Optional[float] = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build a standardized signal dict for the order router and risk controller."""
        signal: dict[str, Any] = {
            "action": action,
            "strategy_name": self.name,
            "market_type": market_type,
            "symbol": self.symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "price": price,
            "client_order_id": client_order_id,
        }
        if leverage is not None:
            signal["leverage"] = leverage
        if notional_usd is not None:
            signal["notional_usd"] = notional_usd
        signal.update(extra)
        return signal
