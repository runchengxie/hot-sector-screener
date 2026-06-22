"""Portfolio construction tools adapted from guan-etf-rotation-v3.

Includes:
  - Covariance shrinkage (diagonal / Ledoit-Wolf style)
  - Mean-variance optimization with turnover penalty
  - Equal-weight portfolio builder
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize


def shrink_covariance_matrix(
    cov: pd.DataFrame | None,
    shrinkage: float = 0.2,
) -> pd.DataFrame | None:
    """Apply diagonal shrinkage to a covariance matrix.

    shrunk = (1 - shrinkage) * cov + shrinkage * diag(cov)

    This stabilizes estimation when N is large relative to T and is the
    simplest form of Ledoit-Wolf shrinkage.

    Args:
        cov: Sample covariance matrix (DataFrame with asset labels).
        shrinkage: Shrinkage intensity in [0, 1].  0 = no shrinkage,
            1 = diagonal only.  Default 0.2 is conservative.

    Returns:
        Shrunk covariance matrix, or None if input is empty.
    """
    if cov is None or cov.empty:
        return cov

    resolved = min(max(float(shrinkage), 0.0), 1.0)
    cov_values = cov.values.astype(float)
    diag_vals = np.diag(np.diag(cov_values))
    shrunk = (1.0 - resolved) * cov_values + resolved * diag_vals

    # Add tiny ridge to ensure positive definiteness
    diag_mean = float(np.nanmean(np.diag(diag_vals))) if diag_vals.size else 0.0
    shrunk = shrunk + np.eye(cov.shape[0]) * (diag_mean * 1e-6 + 1e-12)

    return pd.DataFrame(shrunk, index=cov.index, columns=cov.columns)


def build_equal_weight_portfolio(
    symbols: list[str],
    max_weight_per_asset: float = 1.0,
) -> dict[str, float]:
    """Build an equal-weight portfolio respecting max weight constraints.

    Args:
        symbols: List of asset symbols.
        max_weight_per_asset: Maximum allocation per asset (0-1).

    Returns:
        Dict mapping symbol → weight.
    """
    if not symbols:
        return {}

    n = len(symbols)
    resolved_cap = min(max(float(max_weight_per_asset), 0.0), 1.0)
    if resolved_cap <= 0:
        return {}

    equal_w = min(1.0 / n, resolved_cap)
    return {s: float(equal_w) for s in symbols}


def optimize_portfolio_weights(
    mu: pd.Series,
    cov: pd.DataFrame | None = None,
    max_weight_per_asset: float = 0.35,
    turnover_penalty: float = 0.05,
    current_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Maximize Sharpe ratio with turnover penalty via SLSQP.

    Objective: max Sharpe(w) - turnover_penalty * sum(|w - w_prev|)
    Subject to: sum(w) = 1, 0 <= w_i <= max_weight_per_asset

    Args:
        mu: Expected returns per asset (Series, indexed by symbol).
        cov: Covariance matrix (DataFrame).  If None, falls back to equal weight.
        max_weight_per_asset: Upper bound per asset weight (0-1).
        turnover_penalty: Penalty per unit of turnover (reduces churn).
        current_weights: Previous period weights for turnover calculation.

    Returns:
        Dict mapping symbol → optimized weight.
    """
    symbols = list(mu.index)
    n = len(symbols)
    if n == 0:
        return {}

    resolved_cap = min(max(float(max_weight_per_asset), 0.0), 1.0)
    resolved_penalty = max(float(turnover_penalty), 0.0)

    # If no covariance matrix, fall back to equal weight
    if cov is None or cov.empty:
        return build_equal_weight_portfolio(symbols, max_weight_per_asset=resolved_cap)

    # Current weights as vector
    current = np.array(
        [float((current_weights or {}).get(s, 0.0)) for s in symbols],
        dtype=float,
    )
    if not (np.isfinite(current).all() and current.sum() > 0):
        current = np.zeros(n, dtype=float)
    else:
        current = np.clip(current, 0.0, 1.0)

    # If cap is too tight for N assets, just cap equally
    if resolved_cap > 0 and (resolved_cap * n) < 1.0:
        return {s: float(resolved_cap) for s in symbols}

    w0 = np.ones(n) / n

    def _smooth_abs(x: np.ndarray) -> np.ndarray:
        return np.sqrt(x**2 + 1e-8)

    def _neg_regularized_sharpe(w: np.ndarray) -> float:
        w = np.asarray(w, dtype=float)
        ret = float(mu.values @ w)
        vol = float(np.sqrt(max(w @ cov.values @ w, 1e-12)))
        turnover = float(np.sum(_smooth_abs(w - current)))
        sharpe = ret / (vol + 1e-12)
        return -sharpe + resolved_penalty * turnover

    cons = ({"type": "eq", "fun": lambda w: np.sum(w) - 1.0},)
    bounds = [(0.0, resolved_cap)] * n

    res = minimize(
        _neg_regularized_sharpe,
        w0,
        method="SLSQP",
        bounds=bounds,
        constraints=cons,
        options={"maxiter": 200, "ftol": 1e-9},
    )

    if getattr(res, "success", False) and np.all(np.isfinite(res.x)):
        weights = np.clip(np.asarray(res.x, dtype=float), 0.0, resolved_cap)
    else:
        weights = w0

    w_sum = float(weights.sum())
    if w_sum <= 0:
        weights = w0
        w_sum = float(weights.sum())
    weights = weights / w_sum

    return {s: float(weights[i]) for i, s in enumerate(symbols)}
