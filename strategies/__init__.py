"""Quantitative strategy catalog."""

from strategies.base_strategy import BaseStrategy
from strategies.ma_trend_strategy import MaTrendStrategy

__all__ = ["BaseStrategy", "MaTrendStrategy"]
