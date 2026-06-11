"""Short-term trend-following strategy using MACD and KDJ indicators via SQLite cache."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

import pandas as pd

from config.settings import DB_PATH
from strategies.base_strategy import BaseStrategy
from utils.logger import get_logger

logger = get_logger(__name__)


class MaTrendStrategy(BaseStrategy):
    """
    MACD & KDJ Short-term Trend Strategy.

    Logic:
    - Calculates MACD and KDJ from historical Klines cached in SQLite.
    - Buy (Long) Signal: MACD Golden Cross (Histogram turns positive) AND KDJ J-line slope is upward.
    - Sell (Short/Exit) Signal: MACD Death Cross, -1.5% stop-loss, or +3.0% take-profit.
    """

    def __init__(
        self,
        name: str,
        symbol: str,
        params: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(name, symbol, params)
        # Strategy localized definitions with clean defaults
        self.fast_period = self.params.get("fast_period", 12)
        self.slow_period = self.params.get("slow_period", 26)
        self.signal_period = self.params.get("signal_period", 9)
        self.k_period = self.params.get("k_period", 9)
        self.order_quantity = float(self.params.get("order_quantity", 0.001))
        self.market_type = self.params.get("market_type", "spot")

        self.last_signal_time: Optional[int] = None
        self.has_position = False
        self.entry_price: Optional[float] = None
        self.is_order_pending = False

    async def start(self) -> None:
        """Executed on bootstrapper launch; pulls target constraints if required."""
        await super().start()
        logger.info("Initializing historical state framework for %s", self.name)

    async def stop(self) -> None:
        await super().stop()

    def _calculate_indicators(self) -> Optional[pd.DataFrame]:
        """Extracts data from the SQLite market sliding window to populate vector frames."""
        try:
            conn = sqlite3.connect(DB_PATH)
            query = """
                SELECT open_time, open, high, low, close, volume
                FROM market_kline
                WHERE symbol = ?
                ORDER BY open_time DESC LIMIT 100
            """
            df = pd.read_sql_query(query, conn, params=(self.symbol,))
            conn.close()

            if df.empty or len(df) < max(self.slow_period, self.k_period) + 5:
                return None

            # Re-order row index vectors chronologically from past to present
            df = df.iloc[::-1].reset_index(drop=True)

            # --- Vectorized MACD Computation ---
            exp1 = df["close"].ewm(span=self.fast_period, adjust=False).mean()
            exp2 = df["close"].ewm(span=self.slow_period, adjust=False).mean()
            df["macd"] = exp1 - exp2
            df["signal"] = df["macd"].ewm(span=self.signal_period, adjust=False).mean()
            df["hist"] = df["macd"] - df["signal"]

            # --- Vectorized KDJ Computation ---
            low_min = df["low"].rolling(window=self.k_period).min()
            high_max = df["high"].rolling(window=self.k_period).max()
            df["rsv"] = ((df["close"] - low_min) / (high_max - low_min + 1e-8)) * 100

            k_values, d_values = [], []
            current_k, current_d = 50.0, 50.0

            for rsv in df["rsv"]:
                if pd.isna(rsv):
                    k_values.append(None)
                    d_values.append(None)
                else:
                    current_k = (2 / 3) * current_k + (1 / 3) * rsv
                    current_d = (2 / 3) * current_d + (1 / 3) * current_k
                    k_values.append(current_k)
                    d_values.append(current_d)

            df["k"] = k_values
            df["d"] = d_values
            df["j"] = 3 * df["k"] - 2 * df["d"]

            return df
        except Exception as exc:
            logger.error("Error calculating indicators for %s: %s", self.symbol, exc)
            return None

    async def on_kline_update(self, kline: dict[str, Any]) -> list[dict[str, Any]]:
        """Fires whenever the market core engine updates or caches a new Bar."""
        if not self.is_running or self.is_order_pending:
            return []

        df = self._calculate_indicators()
        if df is None or len(df) < 3:
            return []

        idx_prev = -2

        hist_prev = df["hist"].iloc[idx_prev - 1]
        hist_curr = df["hist"].iloc[idx_prev]

        j_prev = df["j"].iloc[idx_prev - 1]
        j_curr = df["j"].iloc[idx_prev]

        current_close = float(kline["close"])
        client_id = f"cl_{self.name}_{kline['open_time']}"
        signals = []

        # 1. Standard Entry Execution: MACD Golden Cross + KDJ Slope upward
        if not self.has_position:
            macd_golden_cross = (hist_prev <= 0 and hist_curr > 0) or (
                hist_prev > 0 and hist_curr > hist_prev
            )
            kdj_slope_up = j_curr > j_prev and j_curr < 80

            if macd_golden_cross and kdj_slope_up:
                logger.info(
                    "🟢 [%s] Production Trend Buy Triggered. MACD_Hist: %.4f, KDJ_J: %.2f",
                    self.symbol,
                    hist_curr,
                    j_curr,
                )
                signal = self.build_signal(
                    action="create",
                    market_type=self.market_type,
                    side="buy",
                    order_type="market",
                    quantity=self.order_quantity,
                    client_order_id=client_id,
                )
                self.is_order_pending = True
                signals.append(signal)

        # 2. Production Exit Execution: MACD Death Cross OR Defensive Take-Profit / Stop-Loss
        elif self.has_position:
            macd_death_cross = hist_prev >= 0 and hist_curr < 0

            stop_loss_triggered = False
            take_profit_triggered = False

            if self.entry_price:
                return_pct = ((current_close - self.entry_price) / self.entry_price) * 100
                if return_pct <= -1.5:
                    stop_loss_triggered = True
                    logger.warning(
                        "🚨 [%s] Hard Stop Loss Triggered! Return: %.2f%%",
                        self.symbol,
                        return_pct,
                    )
                elif return_pct >= 3.0:
                    take_profit_triggered = True
                    logger.info(
                        "🎯 [%s] Take Profit Target Met! Return: %.2f%%",
                        self.symbol,
                        return_pct,
                    )

            if macd_death_cross or stop_loss_triggered or take_profit_triggered:
                logger.info("🔴 [%s] Production Trend Sell/Exit Triggered.", self.symbol)
                signal = self.build_signal(
                    action="create",
                    market_type=self.market_type,
                    side="sell",
                    order_type="market",
                    quantity=self.order_quantity,
                    client_order_id=client_id,
                )
                self.is_order_pending = True
                signals.append(signal)

        return signals

    async def on_order_status(self, order: dict[str, Any]) -> None:
        """Reconciles position switches and caches entry price for risk exits."""
        status = order.get("status")
        side = order.get("side")

        if status in ("closed", "FILLED", "canceled", "rejected", "CANCELED", "REJECTED"):
            self.is_order_pending = False

        if status in ("closed", "FILLED"):
            if side == "buy":
                self.has_position = True
                self.entry_price = float(order.get("price") or 0.0)
                logger.info(
                    "🛒 [%s] Order Filled successfully: POSITION OPENED at %.4f",
                    self.symbol,
                    self.entry_price,
                )
            elif side == "sell":
                self.has_position = False
                self.entry_price = None
                logger.info(
                    "💰 [%s] Order Filled successfully: POSITION CLOSED",
                    self.symbol,
                )

    async def on_tick(self) -> list[dict[str, Any]]:
        return []
