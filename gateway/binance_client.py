"""CCXT-based Binance spot and futures client with resilient error handling."""

from __future__ import annotations

import time
from typing import Any, Callable, Literal, Optional, TypeVar

import ccxt

from config.settings import load_binance_credentials
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
        file_key, file_secret = load_binance_credentials()
        self.api_key = api_key or file_key or ""
        self.api_secret = api_secret or file_secret or ""
        self.sandbox = sandbox
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries
        self.retry_delay_sec = retry_delay_sec

        self._spot: Optional[ccxt.binance] = None
        self._futures: Optional[ccxt.binance] = None
        self.public_only: bool = False

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _base_config(self, *, require_auth: bool = True) -> dict[str, Any]:
        if require_auth and not self.has_credentials:
            raise AuthenticationError(
                "Binance API credentials missing. Set BINANCE_API_KEY / BINANCE_API_SECRET "
                "or pass api_key/api_secret to BinanceClient."
            )

        config: dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": self.timeout_ms,
            "options": {
                "adjustForTimeDifference": True,
                "recvWindow": 10_000,
            },
        }
        if self.has_credentials:
            config["apiKey"] = self.api_key
            config["secret"] = self.api_secret
        return config

    def _build_exchange(
        self,
        default_type: MarketType,
        *,
        require_auth: bool = True,
    ) -> ccxt.binance:
        config = self._base_config(require_auth=require_auth)
        config["options"] = {
            **config["options"],
            "defaultType": "future" if default_type == "futures" else "spot",
        }
        exchange = ccxt.binance(config)
        if self.sandbox and self.has_credentials:
            exchange.set_sandbox_mode(True)
        return exchange

    @property
    def spot(self) -> ccxt.binance:
        if self._spot is None:
            require_auth = not self.public_only
            self._spot = self._build_exchange("spot", require_auth=require_auth)
        return self._spot

    @property
    def futures(self) -> ccxt.binance:
        if self._futures is None:
            self._futures = self._build_exchange("futures", require_auth=True)
        return self._futures

    def get_exchange(self, market_type: MarketType) -> ccxt.binance:
        return self.futures if market_type == "futures" else self.spot

    def _init_public_spot(self) -> None:
        """Initialize spot client without credentials (public OHLCV only)."""
        self.public_only = True
        self._spot = None
        self._futures = None
        exchange = self._build_exchange("spot", require_auth=False)
        self.safe_call(exchange.load_markets)
        self._spot = exchange
        logger.info("Binance public market-data client initialized")

    def initialize(self) -> None:
        """Load markets; verify balances when API credentials are valid."""
        logger.info("Initializing Binance CCXT clients (sandbox=%s)", self.sandbox)
        if not self.has_credentials:
            logger.warning(
                "No API credentials configured. Running in public market-data mode "
                "(OHLCV sync only; no balance checks or order placement)."
            )
            self._init_public_spot()
            return

        self.public_only = False
        try:
            spot = self.get_exchange("spot")
            self.safe_call(spot.load_markets)
            self.safe_call(spot.fetch_balance)
            logger.info("Binance spot client authenticated successfully")
        except AuthenticationError as exc:
            logger.error(
                "Binance spot authentication failed: %s. "
                "Check secure_keys.json: use testnet keys when BINANCE_SANDBOX=true, "
                "ensure IP whitelist is open, and enable Reading permission.",
                exc,
            )
            logger.warning("Falling back to public market-data mode (no trading).")
            self._init_public_spot()
            return

        try:
            futures = self.get_exchange("futures")
            self.safe_call(futures.load_markets)
            self.safe_call(futures.fetch_balance)
            logger.info("Binance futures client authenticated successfully")
        except AuthenticationError as exc:
            logger.warning(
                "Binance futures authentication skipped: %s. Spot trading remains available.",
                exc,
            )
            self._futures = None

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
