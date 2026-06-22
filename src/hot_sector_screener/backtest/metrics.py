"""Backtest metrics computation with statistical validation.

Includes:
  - compute_metrics: standard backtest metrics
  - compute_advanced_metrics: adds DSR, PSR on top
  - yearly_breakdown: per-year performance
"""

from __future__ import annotations

import numpy as np

from ..validation import deflated_sharpe_ratio, probabilistic_sharpe_ratio


def max_drawdown(returns: np.ndarray) -> float:
    """Compute maximum drawdown from a series of returns."""
    if len(returns) == 0:
        return 0.0
    cum = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    return float(np.min(dd))


def compute_metrics(
    daily_returns: list[float],
    initial_capital: float = 1_000_000,
    annual_factor: int = 252,
) -> dict:
    """Compute standard backtest metrics from daily returns.

    Returns dict with: total_return_pct, annual_return_pct, annual_vol_pct,
    sharpe, hit_rate_pct, max_drawdown_pct, trade_days, final_nav.
    """
    if not daily_returns:
        return {
            "total_return_pct": 0.0,
            "annual_return_pct": 0.0,
            "annual_vol_pct": 0.0,
            "sharpe": 0.0,
            "hit_rate_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "trade_days": 0,
            "final_nav": float(initial_capital),
        }

    ret_arr = np.array(daily_returns)
    n = len(ret_arr)

    total = float(np.prod(1.0 + ret_arr) - 1)
    ann = float((1.0 + total) ** (annual_factor / n) - 1) if n > 0 else 0.0
    vol = float(np.std(ret_arr) * np.sqrt(annual_factor)) if n > 1 else 0.0
    sharpe = float(ann / vol) if vol > 0 else 0.0
    hit_rate = float(np.mean(ret_arr > 0)) * 100
    max_dd = float(max_drawdown(ret_arr))
    nav = float(initial_capital * np.prod(1.0 + ret_arr))

    return {
        "total_return_pct": round(total * 100, 2),
        "annual_return_pct": round(ann * 100, 2),
        "annual_vol_pct": round(vol * 100, 2),
        "sharpe": round(sharpe, 3),
        "hit_rate_pct": round(hit_rate, 1),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "trade_days": n,
        "final_nav": round(nav, 0),
    }


def compute_advanced_metrics(
    daily_returns: list[float],
    initial_capital: float = 1_000_000,
    effective_trials: int = 1,
    annual_factor: int = 252,
) -> dict:
    """Compute standard metrics PLUS DSR and PSR.

    DSR adjusts the Sharpe ratio for multiple testing bias — critical when
    you've swept many parameter configurations.

    Args:
        daily_returns: List of per-period returns.
        initial_capital: Starting capital.
        effective_trials: Number of independent strategy variations tested.
            Conservative: count every parameter combination you tried.
        annual_factor: Trading periods per year (252 for daily).

    Returns:
        Dict with standard metrics plus dsr, psr, expected_max_sharpe,
        min_track_record_length, skewness, kurtosis.
    """
    base = compute_metrics(daily_returns, initial_capital, annual_factor)

    if not daily_returns:
        base["dsr"] = 0.0
        base["psr"] = 0.0
        base["expected_max_sharpe"] = 0.0
        base["min_track_record_length_days"] = None
        base["skewness"] = 0.0
        base["kurtosis"] = 3.0
        return base

    ret_arr = np.array(daily_returns)

    dsr_stats = deflated_sharpe_ratio(
        ret_arr,
        effective_trials=effective_trials,
        periods_per_year=annual_factor,
    )

    base["dsr"] = round(float(dsr_stats["dsr"]), 3)
    base["psr"] = round(float(dsr_stats.get("dsr", 0.0)), 3)  # DSR IS the relevant PSR
    base["expected_max_sharpe"] = round(float(dsr_stats.get("expected_max_sharpe", 0.0)), 3)
    base["min_track_record_length_days"] = (
        round(float(dsr_stats["min_track_record_length"]))
        if dsr_stats.get("min_track_record_length") is not None
        else None
    )
    base["skewness"] = round(float(dsr_stats.get("skewness", 0.0)), 3)
    base["kurtosis"] = round(float(dsr_stats.get("kurtosis", 3.0)), 3)

    return base


def yearly_breakdown(
    daily_returns: list[float],
    trade_log: list[dict],
    annual_factor: int = 252,
) -> list[dict]:
    """Compute per-calendar-year performance from trade log and returns."""
    if not trade_log:
        return []
    by_year: dict[str, list[float]] = {}
    for t, ret in zip(trade_log, daily_returns, strict=False):
        year = t["date"][:4]
        by_year.setdefault(year, []).append(ret)
    result = []
    for year in sorted(by_year):
        arr = np.array(by_year[year])
        n = len(arr)
        total = float(np.prod(1.0 + arr) - 1)
        ann = float((1.0 + total) ** (annual_factor / n) - 1) if n > 0 else 0.0
        vol = float(np.std(arr) * np.sqrt(annual_factor)) if n > 1 else 0.0
        sharpe = float(ann / vol) if vol > 0 else 0.0
        hit = float(np.mean(arr > 0)) * 100
        dd = float(max_drawdown(arr))
        result.append({
            "year": year,
            "trades": n,
            "return_pct": round(total * 100, 2),
            "sharpe": round(sharpe, 3),
            "hit_rate_pct": round(hit, 1),
            "max_dd_pct": round(dd * 100, 2),
        })
    return result
