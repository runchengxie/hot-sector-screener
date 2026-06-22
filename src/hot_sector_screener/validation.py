"""Statistical validation tools adapted from guan-etf-rotation-v3.

Includes:
  - Temporal split (train/purge/val/embargo)
  - Walk-forward window construction
  - Probabilistic Sharpe Ratio (PSR)
  - Deflated Sharpe Ratio (DSR)
  - Daily cross-sectional rank IC computation
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import e, sqrt
from statistics import NormalDist

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

_STANDARD_NORMAL = NormalDist()
_EULER_GAMMA = 0.5772156649015329


# ── Temporal split ──


@dataclass(frozen=True)
class TemporalSplit:
    """A single temporal split with purge and embargo buffers.

    Fields:
        train_dates: Training period dates.
        purge_dates: Buffer between train and validation (prevents label overlap).
        validation_dates: Validation period dates.
        embargo_dates: Post-validation buffer (prevents forward-looking bias).
        train_ratio: Fraction of usable dates used for training.
        purge_days: Configured purge buffer length.
        embargo_days: Configured embargo buffer length.
    """

    train_dates: tuple[pd.Timestamp, ...]
    purge_dates: tuple[pd.Timestamp, ...]
    validation_dates: tuple[pd.Timestamp, ...]
    embargo_dates: tuple[pd.Timestamp, ...]
    train_ratio: float
    purge_days: int
    embargo_days: int


def build_temporal_split(
    sample_dates: Iterable[pd.Timestamp],
    train_ratio: float = 0.75,
    purge_days: int = 3,
    embargo_days: int = 5,
) -> TemporalSplit:
    """Build a single temporal train/validation split with purge and embargo.

    Purge: dates between train end and validation start that may share label
    information with training samples.  Embargo: dates after validation end
    that are too close to validation labels to be safely used.

    Args:
        sample_dates: All available sample dates (sorted deduplicated).
        train_ratio: Fraction of (usable - embargo) dates for training.
        purge_days: Number of dates to reserve as purge buffer.
        embargo_days: Number of dates to reserve as embargo buffer.

    Returns:
        TemporalSplit with four non-overlapping date tuples.
    """
    if not 0 < float(train_ratio) < 1:
        raise ValueError(f"train_ratio must be between 0 and 1, got {train_ratio!r}")

    unique_dates = pd.Index(pd.to_datetime(list(sample_dates))).drop_duplicates().sort_values()
    if len(unique_dates) < 2:
        raise ValueError("Need at least two distinct sample dates to build a temporal split")

    resolved_purge = max(0, int(purge_days))
    resolved_embargo = max(0, int(embargo_days))

    usable_end = len(unique_dates) - resolved_embargo
    if usable_end < 2:
        raise ValueError("Embargo leaves fewer than two dates for train/validation")

    candidate_dates = list(unique_dates[:usable_end])
    validation_start = int(len(candidate_dates) * float(train_ratio))
    validation_start = min(max(validation_start, 1), len(candidate_dates) - 1)

    purge_start = max(0, validation_start - resolved_purge)
    train_dates = tuple(candidate_dates[:purge_start])
    purge_dates_tuple = tuple(candidate_dates[purge_start:validation_start])
    validation_dates = tuple(candidate_dates[validation_start:])
    embargo_dates = tuple(unique_dates[usable_end:])

    if not train_dates:
        raise ValueError("Temporal split produced an empty train set")
    if not validation_dates:
        raise ValueError("Temporal split produced an empty validation set")

    return TemporalSplit(
        train_dates=train_dates,
        purge_dates=purge_dates_tuple,
        validation_dates=validation_dates,
        embargo_dates=embargo_dates,
        train_ratio=float(train_ratio),
        purge_days=resolved_purge,
        embargo_days=resolved_embargo,
    )


# ── Walk-forward windows ──


@dataclass(frozen=True)
class WalkForwardWindow:
    """One fold in a walk-forward backtest.

    train_cutoff_date: All data before this date is available for training.
    test_dates: Dates in this test fold.
    """

    fold_index: int
    train_cutoff_date: pd.Timestamp
    test_dates: tuple[pd.Timestamp, ...]


def build_walk_forward_windows(
    test_dates: Iterable[pd.Timestamp],
    step_days: int = 40,
) -> tuple[WalkForwardWindow, ...]:
    """Build walk-forward test windows from a list of test dates.

    Args:
        test_dates: All backtest dates (sorted deduplicated).
        step_days: Number of trading days per test fold.

    Returns:
        Tuple of WalkForwardWindow objects, one per fold.
    """
    unique_dates = pd.Index(pd.to_datetime(list(test_dates))).drop_duplicates().sort_values()
    if unique_dates.empty:
        return ()

    resolved_step = max(1, int(step_days))
    windows = []
    for fold_index, start_idx in enumerate(range(0, len(unique_dates), resolved_step), start=1):
        fold_test_dates = tuple(unique_dates[start_idx : start_idx + resolved_step])
        windows.append(
            WalkForwardWindow(
                fold_index=fold_index,
                train_cutoff_date=fold_test_dates[0],
                test_dates=fold_test_dates,
            )
        )
    return tuple(windows)


# ── Probabilistic Sharpe Ratio ──


def probabilistic_sharpe_ratio(
    returns: pd.Series | np.ndarray,
    benchmark_sharpe: float = 0.0,
    periods_per_year: int = 252,
    confidence_level: float = 0.95,
) -> dict:
    """Compute PSR — the probability that the true Sharpe exceeds a benchmark.

    Based on Lo (2002) and Bailey & Lopez de Prado (2012).  Uses the
    asymptotic distribution of the Sharpe ratio estimator, adjusting for
    non-normality via skewness and kurtosis.

    Args:
        returns: Series of periodic returns.
        benchmark_sharpe: Sharpe ratio to test against (default 0).
        periods_per_year: Annualization factor (252 for daily).
        confidence_level: For minimum track record calculation.

    Returns:
        Dict with: sample_size, annualized_sharpe, psr, sharpe_std_error,
        min_track_record_length, skewness, kurtosis.
    """
    series = pd.Series(returns, copy=False).dropna()
    n = len(series)
    if n < 2:
        return {
            "sample_size": n,
            "annualized_sharpe": 0.0,
            "psr": 0.0,
            "sharpe_std_error": 0.0,
            "min_track_record_length": None,
            "skewness": 0.0,
            "kurtosis": 3.0,
        }

    vol = float(series.std(ddof=1))
    if vol <= 0:
        return {
            "sample_size": n,
            "annualized_sharpe": 0.0,
            "psr": 0.0,
            "sharpe_std_error": 0.0,
            "min_track_record_length": None,
            "skewness": 0.0,
            "kurtosis": 3.0,
        }

    daily_sharpe = float(series.mean() / vol)
    skew = float(series.skew()) if n >= 3 else 0.0
    kurt = float(series.kurt()) + 3.0 if n >= 4 else 3.0  # excess → regular

    # Asymptotic variance of Sharpe ratio estimator
    denominator = max(
        1.0 - skew * daily_sharpe + ((kurt - 1.0) / 4.0) * (daily_sharpe**2),
        1e-12,
    )
    sharpe_se = sqrt(denominator / max(n - 1, 1))
    z_score = (daily_sharpe - float(benchmark_sharpe)) / sharpe_se
    psr = float(_STANDARD_NORMAL.cdf(z_score))

    # Minimum track record length for statistical significance
    if daily_sharpe <= float(benchmark_sharpe):
        min_track = float("inf")
    else:
        critical = _STANDARD_NORMAL.inv_cdf(confidence_level)
        min_track = 1.0 + denominator * (critical / (daily_sharpe - float(benchmark_sharpe))) ** 2

    return {
        "sample_size": n,
        "annualized_sharpe": daily_sharpe * sqrt(periods_per_year),
        "psr": psr,
        "sharpe_std_error": float(sharpe_se),
        "min_track_record_length": None if min_track == float("inf") else float(min_track),
        "skewness": float(skew),
        "kurtosis": float(kurt),
    }


# ── Deflated Sharpe Ratio ──


def deflated_sharpe_ratio(
    returns: pd.Series | np.ndarray,
    effective_trials: int = 1,
    periods_per_year: int = 252,
    confidence_level: float = 0.95,
) -> dict:
    """Compute DSR — Sharpe adjusted for multiple testing / data mining.

    Uses the extreme value theory approximation from Bailey & Lopez de Prado
    (2014) to estimate the expected maximum Sharpe from N independent trials,
    then tests the observed Sharpe against that deflated benchmark.

    Args:
        returns: Series of periodic returns.
        effective_trials: Number of independent strategy variations tested
            (conservative: count every parameter combination swept).
        periods_per_year: Annualization factor.
        confidence_level: For PSR calculation.

    Returns:
        Dict extending PSR output with effective_trials, expected_max_sharpe,
        and dsr (deflated PSR).
    """
    base = probabilistic_sharpe_ratio(
        returns,
        benchmark_sharpe=0.0,
        periods_per_year=periods_per_year,
        confidence_level=confidence_level,
    )
    resolved_trials = max(1, int(effective_trials))

    expected_max = 0.0
    if resolved_trials > 1 and base["sharpe_std_error"] > 0:
        # EVT approximation: expected max of N independent standard normals
        z_one = _STANDARD_NORMAL.inv_cdf(1.0 - 1.0 / resolved_trials)
        z_two = _STANDARD_NORMAL.inv_cdf(1.0 - 1.0 / (resolved_trials * e))
        expected_max = base["sharpe_std_error"] * (
            (1.0 - _EULER_GAMMA) * z_one + _EULER_GAMMA * z_two
        )

    deflated = probabilistic_sharpe_ratio(
        returns,
        benchmark_sharpe=expected_max,
        periods_per_year=periods_per_year,
        confidence_level=confidence_level,
    )
    base["effective_trials"] = resolved_trials
    base["expected_max_sharpe"] = float(expected_max)
    base["dsr"] = float(deflated["psr"])
    return base


# ── Daily cross-sectional rank IC ──


def compute_daily_rank_ic(
    predictions: np.ndarray | pd.Series,
    targets: np.ndarray | pd.Series,
    sample_dates: Iterable[str | pd.Timestamp],
    min_stocks_per_day: int = 5,
) -> dict:
    """Compute daily cross-sectional rank IC (Spearman correlation).

    This is the CORRECT way to measure ranking quality: compute Spearman
    correlation between predictions and targets for each trading date
    separately, then average.  Do NOT pool all dates and compute a single
    correlation — that destroys the cross-sectional structure and produces
    values 3-5× too low.

    Args:
        predictions: Model predictions (same length as targets).
        targets: Actual returns/labels (same length as predictions).
        sample_dates: Date labels for each prediction-target pair.
        min_stocks_per_day: Minimum stocks required per date to compute IC.

    Returns:
        Dict with: mean_ic, median_ic, ic_std, ic_ir (IC / std), n_days,
        ic_series (list of per-day ICs), positive_ic_ratio.
    """
    frame = pd.DataFrame({
        "date": pd.to_datetime(list(sample_dates)),
        "pred": pd.Series(predictions, dtype=float),
        "target": pd.Series(targets, dtype=float),
    }).dropna()

    daily_ics = []
    for _, group in frame.groupby("date", sort=False):
        if len(group) < min_stocks_per_day:
            continue
        corr, _ = spearmanr(group["pred"], group["target"])
        if not np.isnan(corr):
            daily_ics.append(float(corr))

    if not daily_ics:
        return {
            "mean_ic": 0.0, "median_ic": 0.0, "ic_std": 0.0,
            "ic_ir": 0.0, "n_days": 0, "ic_series": [],
            "positive_ic_ratio": 0.0,
        }

    ic_arr = np.array(daily_ics)
    return {
        "mean_ic": float(np.mean(ic_arr)),
        "median_ic": float(np.median(ic_arr)),
        "ic_std": float(np.std(ic_arr, ddof=1)) if len(ic_arr) > 1 else 0.0,
        "ic_ir": float(np.mean(ic_arr) / np.std(ic_arr, ddof=1)) if len(ic_arr) > 1 and np.std(ic_arr, ddof=1) > 0 else 0.0,
        "n_days": int(len(ic_arr)),
        "ic_series": daily_ics,
        "positive_ic_ratio": float(np.mean(ic_arr > 0)),
    }
