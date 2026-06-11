"""CCXT-based Binance spot and futures client with resilient error handling."""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Literal, Optional, TypeVar

import ccxt

from utils.logger import get_logger

logger = get_logger(__name__)

MarketType = Literal["spot", "futures"]
T = TypeVar("T")


class GatewayError(Exception):
    """Base exception for exchange gateway failures."""


class NetworkError(GatewayError):
    """Raised when network connectivity or request timeout occurs."""


class AuthenticationError(GatewayError):
    """Raised when API credentials are missing or rejected."""


class RateLimitError(GatewayError):
    """Raised when exchange rate limits are exceeded."""


class ExchangeError(GatewayError):
    """Raised for unrecoverable exchange-side errors."""


_RETRYABLE_EXCEPTIONS = (
    ccxt.NetworkError,
    ccxt.RequestTimeout,
    ccxt.DDoSProtection,
    ccxt.ExchangeNotAvailable,
)


def _map_ccxt_exception(exc: Exception) -> GatewayError:
    if isinstance(exc, ccxt.AuthenticationError):
        return AuthenticationError(str(exc))
    if isinstance(exc, ccxt.RateLimitExceeded):
        return RateLimitError(str(exc))
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return NetworkError(str(exc))
    if isinstance(exc, ccxt.ExchangeError):
        return ExchangeError(str(exc))
    return GatewayError(str(exc))


class BinanceClient:
    """Initialize and access Binance spot / USDT-M futures via CCXT."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        *,
        sandbox: bool = False,
        timeout_ms: int = 30_000,
        max_retries: int = 3,
        retry_delay_sec: float = 1.0,
    ) -> None:
        self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        self.sandbox = sandbox
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries
        self.retry_delay_sec = retry_delay_sec

        self._spot: Optional[ccxt.binance] = None
        self._futures: Optional[ccxt.binance] = None

    def _base_config(self) -> dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise AuthenticationError(
                "Binance API credentials missing. Set BINANCE_API_KEY / BINANCE_API_SECRET "
                "or pass api_key/api_secret to BinanceClient."
            )

        return {
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "enableRateLimit": True,
            "timeout": self.timeout_ms,
            "options": {
                "adjustForTimeDifference": True,
                "recvWindow": 10_000,
            },
        }

    def _build_exchange(self, default_type: MarketType) -> ccxt.binance:
        config = self._base_config()
        config["options"] = {
            **config["options"],
            "defaultType": "future" if default_type == "futures" else "spot",
        }
        exchange = ccxt.binance(config)
        if self.sandbox:
            exchange.set_sandbox_mode(True)
        return exchange

    @property
    def spot(self) -> ccxt.binance:
        if self._spot is None:
            self._spot = self._build_exchange("spot")
        return self._spot

    @property
    def futures(self) -> ccxt.binance:
        if self._futures is None:
            self._futures = self._build_exchange("futures")
        return self._futures

    def get_exchange(self, market_type: MarketType) -> ccxt.binance:
        return self.futures if market_type == "futures" else self.spot

    def initialize(self) -> None:
        """Load markets and verify credentials for spot and futures."""
        logger.info("Initializing Binance CCXT clients (sandbox=%s)", self.sandbox)
        for market_type in ("spot", "futures"):
            exchange = self.get_exchange(market_type)
            self.safe_call(exchange.load_markets)
            self.safe_call(exchange.fetch_balance)
        logger.info("Binance clients initialized successfully")

    def safe_call(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute a CCXT call with retry on transient network failures."""
        last_error: Optional[GatewayError] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except ccxt.AuthenticationError as exc:
                mapped = AuthenticationError(str(exc))
                logger.error("Binance authentication failed: %s", exc)
                raise mapped from exc
            except ccxt.RateLimitExceeded as exc:
                mapped = RateLimitError(str(exc))
                logger.warning("Binance rate limit hit: %s", exc)
                raise mapped from exc
            except _RETRYABLE_EXCEPTIONS as exc:
                mapped = NetworkError(str(exc))
                last_error = mapped
                logger.warning(
                    "Binance network error (attempt %s/%s): %s",
                    attempt,
                    self.max_retries,
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_sec * attempt)
                    continue
                raise mapped from exc
            except ccxt.ExchangeError as exc:
                mapped = ExchangeError(str(exc))
                logger.error("Binance exchange error: %s", exc)
                raise mapped from exc
            except Exception as exc:
                mapped = _map_ccxt_exception(exc)
                logger.error("Unexpected gateway error: %s", exc)
                raise mapped from exc

        if last_error is not None:
            raise last_error
        raise GatewayError("safe_call failed without a captured exception")

    def close(self) -> None:
        for exchange in (self._spot, self._futures):
            if exchange is not None:
                exchange.close()
        self._spot = None
        self._futures = None
        logger.debug("Binance CCXT clients closed")

    def __enter__(self) -> BinanceClient:
        self.initialize()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
