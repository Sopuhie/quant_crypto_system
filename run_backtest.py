"""CLI entry point for strategy backtesting on historical Binance data."""

from __future__ import annotations

import argparse
import asyncio

from config.settings import DB_PATH, KLINE_INTERVAL
from engine.backtester import run_backtest
from strategies.ma_trend_strategy import MaTrendStrategy
from utils.logger import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest MACD/KDJ trend strategy")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair")
    parser.add_argument("--timeframe", default=KLINE_INTERVAL, help="K-line interval")
    parser.add_argument("--days", type=int, default=7, help="Backtest lookback days")
    parser.add_argument("--capital", type=float, default=10_000.0, help="Initial USDT capital")
    parser.add_argument(
        "--commission",
        type=float,
        default=0.001,
        help="Taker fee rate per side (default 0.1%%)",
    )
    parser.add_argument(
        "--quantity",
        type=float,
        default=0.0005,
        help="Order size in base asset (BTC)",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Use cached K-lines from data/quant.db instead of downloading",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    strategy = MaTrendStrategy(
        name="BTC_ShortTerm_Trend",
        symbol=args.symbol,
        params={
            "market_type": "spot",
            "order_quantity": args.quantity,
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
        },
    )

    result = await run_backtest(
        strategy,
        symbol=args.symbol,
        timeframe=args.timeframe,
        days=args.days,
        initial_capital=args.capital,
        commission_rate=args.commission,
        from_db=DB_PATH if args.from_db else None,
    )
    result.print_summary()


def main() -> None:
    setup_logging()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
