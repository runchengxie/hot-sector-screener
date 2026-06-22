"""ML-enhanced hotspot → ETF rotation backtest.

Combines concept-based scoring with technical feature engineering and
walk-forward model training.  Replaces the deterministic concept→exposure
dot product with a learned ranking model.

Pipeline:
  1. Load hotspot concepts and ETF price data
  2. Compute technical features for each ETF
  3. Build concept-derived features (dimension scores)
  4. Walk-forward backtest: train on expanding window, predict next fold
  5. Compare against baseline (concept-only) and benchmark
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..data_quality import detect_suspicious_price_jumps
from ..features import calculate_technical_features, resolve_feature_columns
from ..portfolio import build_equal_weight_portfolio, shrink_covariance_matrix
from ..training import (
    DEFAULT_STRATEGY_PARAMS,
    build_training_data,
    compute_feature_importance,
    preprocess_inference_features,
    train_model,
)
from ..validation import build_walk_forward_windows, compute_daily_rank_ic
from .etf_backtest import (
    CONCEPT_EXPOSURE_MAP,
    ETF_METADATA,
    _load_ths_hot_concepts,
    _score_etfs,
)
from .metrics import compute_advanced_metrics


ROTATION_ROOT = Path(
    os.environ.get("ETF_ROTATION_ROOT", "/home/richard/code/guan-etf-rotation-v3")
)


# ── ETF data loading ──


def _load_etf_csv(symbol: str) -> pd.DataFrame | None:
    """Load an ETF CSV file from rotation-v3's data/raw/etf/."""
    path = ROTATION_ROOT / "data" / "raw" / "etf" / f"{symbol}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date")
    except Exception:
        return None


def _build_date_list(start_date: str, end_date: str) -> list[str]:
    """Build sorted YYYYMMDD trade-date range from ths_hot data lake."""
    from ..data_sources.platform import list_available_dates

    all_dates = list_available_dates("ths_hot")
    if not all_dates:
        return []
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")
    return sorted(d for d in all_dates if s <= d <= e)


# ── Feature building for ML backtest ──


def _build_concept_features_per_date(
    concepts: list[str],
) -> dict[str, float]:
    """Build concept-derived dimension scores as feature dict.

    Aggregates concept exposure weights into dimension scores, exactly
    like the deterministic concept→ETF scoring does, but returns them
    as a flat feature vector that the ML model can learn weights for.

    Returns:
        Dict of dimension_name → aggregate_score.
    """
    dim_scores: dict[str, float] = {}
    for c in concepts:
        for keyword, dims in CONCEPT_EXPOSURE_MAP.items():
            if keyword.lower() in c.lower() or c.lower() in keyword.lower():
                for dim, weight in dims.items():
                    dim_scores[dim] = dim_scores.get(dim, 0) + weight
    return dim_scores


def _merge_features(
    sym: str,
    tech_feat_df: pd.DataFrame,
    concept_dims: dict[str, float],
    date: pd.Timestamp,
    all_dim_names: list[str],
) -> dict[str, float] | None:
    """Merge technical features and concept dimension scores for one ETF.

    Args:
        sym: ETF symbol.
        tech_feat_df: Technical features DataFrame (date-indexed).
        concept_dims: Dimension scores from today's concepts.
        date: The trading date.
        all_dim_names: Complete list of dimension names (for consistent columns).

    Returns:
        Merged feature dict or None if technical features unavailable.
    """
    if tech_feat_df is None or tech_feat_df.empty:
        return None
    if date not in tech_feat_df.index:
        return None

    etf_meta = ETF_METADATA.get(sym, {})
    etf_exposures = etf_meta.get("exposures", {})

    # Technical features
    tech_row = tech_feat_df.loc[date]
    tech_feats = {
        col: float(tech_row.get(col, 0.0))
        for col in resolve_feature_columns("small_pool")
    }

    # Concept dimension features (dot product pre-computation for ML)
    concept_feats: dict[str, float] = {}
    for dim in all_dim_names:
        dim_score = concept_dims.get(dim, 0.0)
        etf_weight = etf_exposures.get(dim, 0.0)
        concept_feats[f"concept_{dim}"] = dim_score * etf_weight

    return {**tech_feats, **concept_feats}


def _collect_dimension_names() -> list[str]:
    """Collect all unique dimension names from CONCEPT_EXPOSURE_MAP."""
    dims: set[str] = set()
    for dim_map in CONCEPT_EXPOSURE_MAP.values():
        dims.update(dim_map.keys())
    return sorted(dims)


# ── Main ML backtest ──


def run_etf_ml_backtest(
    start_date: str = "2024-10-14",
    end_date: str = "2026-04-30",
    top_k: int = 3,
    fee_rate: float = 0.0005,
    initial_capital: float = 1_000_000,
    model_type: str = "linear_rank",
    walk_forward_step_days: int = 40,
    min_train_days: int = 120,
    purge_days: int = 3,
    embargo_days: int = 5,
    effective_trials: int = 10,
) -> dict[str, Any]:
    """Run ML-enhanced hotspot→ETF rotation backtest with walk-forward training.

    For each walk-forward fold:
      1. Train model on all data before fold start (purged/embargoed)
      2. For each date in the fold:
         a. Get today's hotspot concepts → build dimension scores
         b. Compute technical features for each ETF
         c. Merge features, predict ranking with model
         d. Select top K ETFs, compute next-day return
      3. Compare with baseline (concept-only equal weight)

    Args:
        start_date, end_date: Backtest period.
        top_k: Number of ETFs to hold.
        fee_rate: Per-side fee (0.05% default).
        initial_capital: Starting capital.
        model_type: "linear_rank" or "lightgbm_regression".
        walk_forward_step_days: Trading days per test fold.
        min_train_days: Minimum training history before first fold.
        purge_days: Buffer between train and test (prevents label overlap).
        embargo_days: Not used directly here but passed to label building.
        effective_trials: For DSR calculation.

    Returns:
        Dict with strategy, baseline, benchmark, and fold-level metrics.
    """
    print(f"\n{'='*60}")
    print(f"  ML-Enhanced Hotspot → ETF Rotation Backtest")
    print(f"  Model: {model_type}, Walk-forward steps: {walk_forward_step_days}d")
    print(f"  Period: {start_date} to {end_date}")
    print(f"{'='*60}\n")

    # 1. Load data
    print("1. Loading ETF data...")
    date_list = _build_date_list(start_date, end_date)
    if not date_list:
        return {"error": "No trading dates in range"}

    etf_data: dict[str, pd.DataFrame] = {}
    for sym in ETF_METADATA:
        df = _load_etf_csv(sym)
        if df is not None:
            # Trim to backtest range
            mask = (df["date"] >= pd.to_datetime(start_date)) & (df["date"] <= pd.to_datetime(end_date))
            df = df[mask]
            if not df.empty:
                etf_data[sym] = df.set_index("date").sort_index()

    print(f"  Loaded {len(etf_data)} ETFs, {len(date_list)} trading days")

    # 2. Data quality check
    print("\n2. Data quality check...")
    quality_issues = detect_suspicious_price_jumps(etf_data)
    if quality_issues:
        print(f"  ⚠ Found {len(quality_issues)} ETFs with suspicious price jumps:")
        for issue in quality_issues[:3]:
            print(f"    {issue['symbol']}: {issue['worst_close_to_close_return_pct']:+.1f}% "
                  f"on {issue['worst_jump_date'].strftime('%Y-%m-%d')}")

    # 3. Pre-compute technical features for all ETFs
    print("\n3. Computing technical features...")
    tech_features: dict[str, pd.DataFrame] = {}
    for sym, df in etf_data.items():
        if df is not None and not df.empty and len(df) >= 60:
            try:
                tech_features[sym] = calculate_technical_features(df)
            except Exception as e:
                print(f"    Failed {sym}: {e}")

    print(f"  Features computed for {len(tech_features)} ETFs")

    # 4. Build walk-forward folds
    print("\n4. Building walk-forward folds...")
    all_dates = [pd.Timestamp(f"{d[:4]}-{d[4:6]}-{d[6:]}") for d in date_list]
    test_dates = all_dates[min_train_days:]  # Skip initial warmup

    params = dict(DEFAULT_STRATEGY_PARAMS)
    params.update({
        "model_type": model_type,
        "min_history_days": min_train_days,
        "purge_days": purge_days,
        "embargo_days": embargo_days,
        "feature_set": "small_pool",
        "cross_sectional_feature_scaling": True,
        "label_mode": "next_open_to_open",
        "linear_rank_alpha": 10.0,
    })

    # Collect all dimension names for consistent feature columns
    all_dims = _collect_dimension_names()
    feat_cols = resolve_feature_columns("small_pool")
    all_feature_cols = feat_cols + [f"concept_{d}" for d in all_dims]

    # 5. Walk-forward backtest
    print("\n5. Running walk-forward backtest...")
    fold_results: list[dict] = []
    all_ml_returns: list[float] = []
    all_baseline_returns: list[float] = []
    all_predictions: list[float] = []
    all_targets: list[float] = []
    all_pred_dates: list[pd.Timestamp] = []
    trade_log: list[dict] = []
    ml_nav = initial_capital
    baseline_nav = initial_capital

    # Walk forward: sliding window of step_days
    fold_starts = list(range(min_train_days, len(all_dates), walk_forward_step_days))
    if fold_starts and fold_starts[-1] < len(all_dates) - 1:
        fold_starts.append(len(all_dates) - 1)

    print(f"  Folds: {len(fold_starts) - 1}")

    for fold_idx in range(len(fold_starts) - 1):
        fold_start_idx = fold_starts[fold_idx]
        fold_end_idx = min(fold_starts[fold_idx + 1], len(all_dates) - 1)
        train_cutoff = all_dates[fold_start_idx]
        fold_test_dates = all_dates[fold_start_idx : fold_end_idx + 1]

        print(f"\n  Fold {fold_idx + 1}: train through {train_cutoff.strftime('%Y-%m-%d')}, "
              f"test {len(fold_test_dates)} days")

        # Train model on data up to train_cutoff
        train_data = build_training_data(
            etf_data,
            params=params,
            train_end_date=train_cutoff,
        )

        if train_data is None:
            print(f"    Skipping fold — insufficient training data")
            continue

        X_train, y_train, train_dates, train_symbols = train_data

        # Temporal split for validation within training window
        model, scores = train_model(
            X_train, y_train, train_dates,
            params=params,
        )

        fold_ic = scores.get("train", {}).get("mean_rank_ic", 0.0)
        print(f"    Model trained: {len(X_train)} samples, train IC={fold_ic:.4f}")

        # Predict on fold test dates
        for i, test_date in enumerate(fold_test_dates):
            if test_date not in all_dates:
                continue
            date_idx = all_dates.index(test_date)
            if date_idx + 2 >= len(all_dates):
                continue

            date_str = test_date.strftime("%Y-%m-%d")

            # Get concepts for this date
            concepts = _load_ths_hot_concepts(date_str)
            if not concepts:
                continue

            concept_dims = _build_concept_features_per_date(concepts)

            # Build feature vectors for all ETFs
            features_rows: dict[str, dict[str, float]] = {}
            for sym in ETF_METADATA:
                feat_dict = _merge_features(
                    sym, tech_features.get(sym), concept_dims, test_date, all_dims
                )
                if feat_dict is not None:
                    features_rows[sym] = feat_dict

            if len(features_rows) < top_k:
                continue

            # Predict with model
            X_infer = pd.DataFrame.from_dict(features_rows, orient="index")
            X_infer = preprocess_inference_features(
                X_infer,
                feature_names=all_feature_cols,
                cross_sectional_scaling=True,
            )

            if isinstance(model, object) and hasattr(model, "predict"):
                predictions = model.predict(X_infer)
            else:
                continue

            pred_series = pd.Series(predictions, index=X_infer.index)
            ranked = pred_series.sort_values(ascending=False)

            # ML selection: top K ETFs by model score
            selected_ml = ranked.head(top_k).index.tolist()

            # Baseline selection: top K ETFs by concept score
            concept_scores = _score_etfs(concepts)
            ranked_baseline = sorted(concept_scores.items(), key=lambda x: -x[1])
            selected_baseline = [s for s, sc in ranked_baseline if sc > 0][:top_k]

            # Compute next-day return for ML selection
            entry_idx = date_idx + 1
            exit_idx = date_idx + 2
            entry_date = all_dates[entry_idx]
            exit_date = all_dates[exit_idx]

            ml_ret = _compute_portfolio_return(
                selected_ml, entry_date, exit_date, etf_data, fee_rate
            )
            bl_ret = _compute_portfolio_return(
                selected_baseline, entry_date, exit_date, etf_data, fee_rate
            )

            if ml_ret is not None:
                all_ml_returns.append(ml_ret)
                ml_nav *= 1 + ml_ret
                trade_log.append({
                    "date": entry_date.strftime("%Y-%m-%d"),
                    "fold": fold_idx + 1,
                    "etfs": selected_ml,
                    "return_pct": round(ml_ret * 100, 2),
                })

            if bl_ret is not None:
                all_baseline_returns.append(bl_ret)
                baseline_nav *= 1 + bl_ret

        fold_results.append({
            "fold": fold_idx + 1,
            "train_end": train_cutoff.strftime("%Y-%m-%d"),
            "test_days": len(fold_test_dates),
            "train_ic": round(fold_ic, 4),
            "train_samples": len(X_train),
        })

    # 6. Build results
    print("\n6. Computing metrics...")
    strategy_metrics = compute_advanced_metrics(
        all_ml_returns, initial_capital,
        effective_trials=effective_trials,
    )
    baseline_metrics = compute_advanced_metrics(
        all_baseline_returns, initial_capital,
        effective_trials=1,
    )

    # CSI 300 benchmark
    bm_data = None
    for sym in ("159919", "510300"):
        df = etf_data.get(sym)
        if df is not None and not df.empty:
            bm_data = df
            break

    bm_total = _compute_benchmark(bm_data, start_date, end_date)

    # ETF selection frequency
    etf_counter: Counter[str] = Counter()
    for t in trade_log:
        for s in t["etfs"]:
            etf_counter[s] += 1

    return {
        "period": f"{start_date} to {end_date}",
        "config": {
            "model_type": model_type,
            "top_k": top_k,
            "walk_forward_step_days": walk_forward_step_days,
            "min_train_days": min_train_days,
            "folds": len(fold_results),
        },
        "strategy": {
            "name": f"ML-Enhanced Hotspot→ETF ({model_type})",
            **strategy_metrics,
        },
        "baseline": {
            "name": "Concept-only (equal weight)",
            **baseline_metrics,
        },
        "benchmark": bm_total,
        "excess_vs_baseline_pct": round(
            strategy_metrics["total_return_pct"] - baseline_metrics["total_return_pct"], 2
        ),
        "folds": fold_results,
        "most_selected_etfs": dict(etf_counter.most_common(8)),
        "recent_trades": trade_log[-10:] if len(trade_log) >= 10 else trade_log,
    }


def _compute_portfolio_return(
    symbols: list[str],
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    etf_data: dict[str, pd.DataFrame],
    fee_rate: float,
) -> float | None:
    """Compute equal-weight portfolio return: open entry → open exit."""
    entry_prices: dict[str, float] = {}
    for sym in symbols:
        df = etf_data.get(sym)
        if df is None:
            continue
        if entry_date not in df.index:
            continue
        px = float(df.loc[entry_date, "open"])
        if px > 0:
            entry_prices[sym] = px

    if not entry_prices:
        return None

    port_ret = 0.0
    valid = 0
    for sym, px_in in entry_prices.items():
        df = etf_data.get(sym)
        if df is None:
            continue
        if exit_date not in df.index:
            continue
        px_out = float(df.loc[exit_date, "open"])
        if px_out <= 0:
            continue
        ret = px_out / px_in - 1 - fee_rate * 2
        port_ret += ret
        valid += 1

    if valid == 0:
        return None
    return port_ret / valid


def _compute_benchmark(
    df: pd.DataFrame | None,
    start_date: str,
    end_date: str,
) -> dict | None:
    """Compute buy & hold CSI 300 ETF return."""
    if df is None or df.empty:
        return None
    s = pd.to_datetime(start_date)
    e = pd.to_datetime(end_date)
    df_range = df[(df.index >= s) & (df.index <= e)]
    if len(df_range) < 2:
        return None
    entry_px = float(df_range.iloc[0]["open"])
    exit_px = float(df_range.iloc[-1]["close"])
    total = exit_px / entry_px - 1
    n_days = len(df_range)
    ann = float((1 + total) ** (252 / n_days) - 1) if n_days > 0 else 0
    return {
        "label": "沪深300 ETF 买入持有",
        "total_return_pct": round(total * 100, 2),
        "annual_return_pct": round(ann * 100, 2),
    }
