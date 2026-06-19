"""Backtest package — hotspot-driven strategy backtesting."""

from .etf_backtest import run_etf_backtest
from .metrics import compute_metrics, max_drawdown, yearly_breakdown
from .stock_backtest import run_stock_backtest

__all__ = [
    "compute_metrics",
    "max_drawdown",
    "run_etf_backtest",
    "run_stock_backtest",
    "yearly_breakdown",
]
