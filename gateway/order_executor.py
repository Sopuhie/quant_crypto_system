"""Order placement and cancellation for Binance spot and USDT-M futures."""

from __future__ import annotations

from typing import Any, Literal, Optional

from gateway.binance_client import BinanceClient, MarketType
from utils.logger import get_logger

logger = get_logger(__name__)

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]


class OrderExecutor:
    """Translate strategy intents into CCXT create/cancel order calls."""

    def __init__(self, client: BinanceClient) -> None:
        self.client = client

    def _build_params(
        self,
        client_order_id: Optional[str],
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = dict(extra or {})
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return params

    def _normalize_order(self, raw: dict[str, Any], market_type: MarketType) -> dict[str, Any]:
        return {
            "market_type": market_type,
            "exchange_order_id": raw.get("id"),
            "client_order_id": raw.get("clientOrderId") or raw.get("info", {}).get("clientOrderId"),
            "symbol": raw.get("symbol"),
            "side": raw.get("side"),
            "order_type": raw.get("type"),
            "price": raw.get("price"),
            "quantity": raw.get("amount"),
            "filled_quantity": raw.get("filled"),
            "status": raw.get("status"),
            "raw": raw,
        }

    def create_order(
        self,
        market_type: MarketType,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        *,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        exchange = self.client.get_exchange(market_type)
        order_params = self._build_params(client_order_id, params)

        if order_type == "limit" and price is None:
            raise ValueError("Limit orders require a price")

        logger.info(
            "Creating %s order: symbol=%s side=%s type=%s qty=%s price=%s client_id=%s",
            market_type,
            symbol,
            side,
            order_type,
            quantity,
            price,
            client_order_id,
        )

        raw = self.client.safe_call(
            exchange.create_order,
            symbol,
            order_type,
            side,
            quantity,
            price,
            order_params,
        )
        normalized = self._normalize_order(raw, market_type)
        logger.info(
            "Order created: exchange_id=%s client_id=%s status=%s",
            normalized["exchange_order_id"],
            normalized["client_order_id"],
            normalized["status"],
        )
        return normalized

    def create_spot_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        *,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self.create_order(
            "spot",
            symbol,
            side,
            order_type,
            quantity,
            price=price,
            client_order_id=client_order_id,
            params=params,
        )

    def create_futures_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        *,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self.create_order(
            "futures",
            symbol,
            side,
            order_type,
            quantity,
            price=price,
            client_order_id=client_order_id,
            params=params,
        )

    def cancel_order(
        self,
        market_type: MarketType,
        symbol: str,
        *,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if not order_id and not client_order_id:
            raise ValueError("Either order_id or client_order_id is required")

        exchange = self.client.get_exchange(market_type)
        cancel_params = dict(params or {})
        if client_order_id:
            cancel_params["origClientOrderId"] = client_order_id

        logger.info(
            "Cancelling %s order: symbol=%s order_id=%s client_id=%s",
            market_type,
            symbol,
            order_id,
            client_order_id,
        )

        raw = self.client.safe_call(
            exchange.cancel_order,
            order_id,
            symbol,
            cancel_params,
        )
        normalized = self._normalize_order(raw, market_type)
        logger.info(
            "Order cancelled: exchange_id=%s client_id=%s status=%s",
            normalized["exchange_order_id"],
            normalized["client_order_id"],
            normalized["status"],
        )
        return normalized

    def cancel_spot_order(
        self,
        symbol: str,
        *,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self.cancel_order(
            "spot",
            symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            params=params,
        )

    def cancel_futures_order(
        self,
        symbol: str,
        *,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self.cancel_order(
            "futures",
            symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            params=params,
        )

    def fetch_order(
        self,
        market_type: MarketType,
        symbol: str,
        *,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if not order_id and not client_order_id:
            raise ValueError("Either order_id or client_order_id is required")

        exchange = self.client.get_exchange(market_type)
        fetch_params = dict(params or {})
        if client_order_id:
            fetch_params["origClientOrderId"] = client_order_id

        raw = self.client.safe_call(
            exchange.fetch_order,
            order_id,
            symbol,
            fetch_params,
        )
        return self._normalize_order(raw, market_type)
