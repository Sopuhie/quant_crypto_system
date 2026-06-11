"""Performance metrics compiled from completed trade orders in SQLite."""

from __future__ import annotations

import sqlite3
from typing import Any

from config.settings import DB_PATH
from utils.logger import get_logger

logger = get_logger(__name__)


class TradeAnalytics:
    """Calculate win rate, realized PnL, and profit factor from filled orders."""

    @staticmethod
    def calculate_metrics(db_path: str | None = None) -> dict[str, Any]:
        """Compile execution orders to determine strategy performance."""
        metrics: dict[str, Any] = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "realized_pnl": 0.0,
            "profit_factor": 0.0,
        }

        path = db_path or str(DB_PATH)

        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT side, price, quantity, filled_quantity, status
                FROM trade_orders
                WHERE status IN ('closed', 'FILLED')
                ORDER BY id ASC
                """
            )
            orders = cursor.fetchall()
            conn.close()

            if not orders:
                return metrics

            total_wins = 0
            total_losses = 0
            gross_profit = 0.0
            gross_loss = 0.0
            position_entry: float | None = None

            for order in orders:
                side = str(order["side"]).lower()
                price = float(order["price"] or 0.0)
                qty = float(order["filled_quantity"] or order["quantity"] or 0.0)

                if side == "buy":
                    position_entry = price
                elif side == "sell" and position_entry is not None:
                    trade_pnl = (price - position_entry) * qty
                    metrics["total_trades"] += 1
                    metrics["realized_pnl"] += trade_pnl

                    if trade_pnl > 0:
                        total_wins += 1
                        gross_profit += trade_pnl
                    else:
                        total_losses += 1
                        gross_loss += abs(trade_pnl)

                    position_entry = None

            if metrics["total_trades"] > 0:
                metrics["winning_trades"] = total_wins
                metrics["losing_trades"] = total_losses
                metrics["win_rate"] = round((total_wins / metrics["total_trades"]) * 100, 2)
                metrics["profit_factor"] = (
                    round(gross_profit / gross_loss, 2)
                    if gross_loss > 0
                    else round(gross_profit, 2)
                )
                metrics["realized_pnl"] = round(float(metrics["realized_pnl"]), 4)

        except Exception as exc:
            logger.warning("Failed to calculate trade analytics: %s", exc)

        return metrics
