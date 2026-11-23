"""P4 - Backtest & đánh giá (tuân docs/prereg-danh-gia.md)."""

from .engine import BacktestResult, Portfolio, Position, Trade, View, run_backtest

__all__ = ["BacktestResult", "Portfolio", "Position", "Trade", "View", "run_backtest"]
