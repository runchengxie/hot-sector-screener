"""ML model training pipeline adapted from guan-etf-rotation-v3.

Supports three model types:
  1. linear_rank    — Ridge regression with cross-sectional target transform
  2. lightgbm_regression — Gradient boosting regression
  3. lightgbm_ranker    — LambdaRank for learning-to-rank

All models operate on daily OHLCV data frames keyed by symbol.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# LightGBM is optional — only needed for lightgbm_* model types
try:
    import lightgbm as lgb  # type: ignore[import-untyped]
    _HAS_LIGHTGBM = True
except ImportError:
    _HAS_LIGHTGBM = False

from .features import (
    ALL_FEATURE_COLUMNS,
    SMALL_POOL_FEATURE_COLUMNS,
    calculate_technical_features,
    resolve_feature_columns,
)
from .validation import build_temporal_split, compute_daily_rank_ic

# Default training hyperparameters (matching rotation-v3 defaults)
DEFAULT_STRATEGY_PARAMS = {
    "model_type": "linear_rank",
    "feature_set": "small_pool",
    "cross_sectional_feature_scaling": True,
    "label_mode": "next_open_to_open",
    "linear_rank_alpha": 10.0,
    "min_history_days": 120,
    "purge_days": 3,
    "embargo_days": 5,
    "train_start_ratio": 0.75,
    "lightgbm_learning_rate": 0.02,
    "lightgbm_num_leaves": 15,
    "lightgbm_max_depth": 4,
    "lightgbm_min_data_in_leaf": 250,
    "lightgbm_feature_fraction": 0.6,
    "lightgbm_bagging_fraction": 0.6,
    "lightgbm_bagging_freq": 5,
    "lightgbm_lambda_l1": 10.0,
    "lightgbm_lambda_l2": 20.0,
    "lightgbm_min_gain_to_split": 0.1,
    "lightgbm_num_boost_round": 300,
    "lightgbm_early_stopping_rounds": 30,
    "effective_trials": 10,
}


# ── Linear Rank Model ──


@dataclass
class LinearRankModel:
    """Ridge regression model for cross-sectional ranking.

    Trained on cross-sectionally demeaned targets (each date's returns
    are centered to zero mean) so the model learns RELATIVE ranking,
    not absolute return prediction.
    """

    feature_names: list[str]
    coefficients: np.ndarray
    intercept: float
    ridge_alpha: float

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Predict ranking scores.

        Args:
            X: Feature matrix (DataFrame or ndarray).

        Returns:
            1-D array of prediction scores.
        """
        if isinstance(X, pd.DataFrame):
            frame = X.reindex(columns=self.feature_names, fill_value=0.0).fillna(0.0)
            values = frame.to_numpy(dtype=float)
        else:
            values = np.asarray(X, dtype=float)
        return values @ self.coefficients + float(self.intercept)


# ── Feature preprocessing ──


def preprocess_training_features(
    X: pd.DataFrame,
    sample_dates: list[pd.Timestamp],
    feature_names: list[str] | None = None,
    cross_sectional_scaling: bool = True,
) -> pd.DataFrame:
    """Preprocess feature matrix for training.

    When cross_sectional_scaling is True, each feature is z-scored
    WITHIN each trading date (cross-sectional normalization).  This
    removes market-level shifts so the model learns relative rankings.

    Args:
        X: Raw feature DataFrame.
        sample_dates: Date per sample, used for cross-sectional grouping.
        feature_names: Columns to keep (default: all columns in X).
        cross_sectional_scaling: If True, apply per-date z-score normalization.

    Returns:
        Preprocessed feature DataFrame.
    """
    selected = list(feature_names or list(X.columns))
    frame = X.reindex(columns=selected, fill_value=0.0).astype(float).copy()

    if cross_sectional_scaling:
        group_keys = pd.Index(pd.to_datetime(sample_dates), name="sample_date")
        grouped = frame.groupby(group_keys)
        mean = grouped.transform("mean")
        std = grouped.transform("std").replace(0.0, np.nan)
        frame = (frame - mean) / std

    return frame.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def preprocess_inference_features(
    feature_frame: pd.DataFrame,
    feature_names: list[str] | None = None,
    cross_sectional_scaling: bool = True,
) -> pd.DataFrame:
    """Preprocess a single-date feature frame for inference.

    Uses per-column (cross-sectional) z-scoring since we only have one
    date's worth of assets.

    Args:
        feature_frame: One row per asset, columns = features.
        feature_names: Columns to keep.
        cross_sectional_scaling: If True, apply cross-asset z-score normalization.

    Returns:
        Preprocessed feature DataFrame.
    """
    selected = list(feature_names or list(feature_frame.columns))
    frame = feature_frame.reindex(columns=selected, fill_value=0.0).astype(float).copy()

    if cross_sectional_scaling:
        mean = frame.mean(axis=0)
        std = frame.std(axis=0).replace(0.0, np.nan)
        frame = (frame - mean) / std

    return frame.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def transform_cross_sectional_target(
    y: pd.Series,
    sample_dates: list[pd.Timestamp],
) -> pd.Series:
    """Cross-sectionally demean and rescale targets per date.

    For ranking models, this removes the date-level mean return so the
    model learns "which asset is better than average today", not "which
    date has higher average returns."

    Args:
        y: Raw target (e.g. next-day returns).
        sample_dates: Date per sample.

    Returns:
        Cross-sectionally normalized targets.
    """
    series = pd.Series(y, dtype=float).reset_index(drop=True)
    group_keys = pd.Index(pd.to_datetime(sample_dates), name="sample_date")
    grouped = series.groupby(group_keys)
    centered = series - grouped.transform("mean")
    scale = grouped.transform("std").replace(0.0, np.nan)
    return (centered / scale).replace([np.inf, -np.inf], np.nan).fillna(0.0)


# ── Training data preparation ──


def build_training_data(
    data_dict: dict[str, pd.DataFrame],
    params: dict | None = None,
    train_end_date: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.Series, list[pd.Timestamp], list[str]] | None:
    """Build a labeled training dataset from OHLCV histories.

    For each trading date with at least min_history_days of prior data,
    compute features and next-day labels for every asset.  The result
    is a stacked DataFrame with one row per (date, asset) pair.

    Args:
        data_dict: {symbol: OHLCV DataFrame with date index}.
        params: Strategy parameters (see DEFAULT_STRATEGY_PARAMS).
        train_end_date: If set, only build samples up to this date.

    Returns:
        (X, y, sample_dates, symbols) or None if not enough data.
    """
    cfg = dict(DEFAULT_STRATEGY_PARAMS)
    if params:
        cfg.update(params)

    min_history = int(cfg["min_history_days"])
    label_mode = str(cfg.get("label_mode", "next_open_to_open"))
    feature_set = str(cfg.get("feature_set", "small_pool"))
    feat_cols = resolve_feature_columns(feature_set)

    # Collect all dates across all assets
    all_dates = sorted({
        d for df in data_dict.values()
        if df is not None and not df.empty
        for d in df.index
    })
    if len(all_dates) <= min_history + 3:
        return None

    if train_end_date is not None:
        all_dates = [d for d in all_dates if d <= train_end_date]

    # Pre-compute features for every asset
    print(f"  Computing features for {len(data_dict)} assets...")
    features_cache: dict[str, pd.DataFrame] = {}
    for sym, df in data_dict.items():
        if df is None or df.empty or len(df) < min_history:
            continue
        try:
            features_cache[sym] = calculate_technical_features(df)
        except Exception:
            continue

    print(f"  Features computed for {len(features_cache)} assets")

    # Walk through dates building labeled samples
    all_features: list[dict[str, float]] = []
    all_labels: list[float] = []
    all_sample_dates: list[pd.Timestamp] = []
    all_symbols: list[str] = []

    for i in range(min_history, len(all_dates) - 1):
        current_date = all_dates[i]
        next_date = all_dates[i + 1]

        for sym, df in data_dict.items():
            if df is None or df.empty:
                continue
            if current_date not in df.index or next_date not in df.index:
                continue

            feat_df = features_cache.get(sym)
            if feat_df is None or current_date not in feat_df.index:
                continue

            # Label: next-day open → hold-period exit
            next_open = float(df.loc[next_date, "open"])
            if next_open <= 0:
                continue

            if label_mode == "next_open_to_open":
                exit_idx = i + 2
                if exit_idx >= len(all_dates):
                    continue
                exit_date = all_dates[exit_idx]
                if exit_date not in df.index:
                    continue
                exit_open = float(df.loc[exit_date, "open"])
                if exit_open <= 0:
                    continue
                label = (exit_open / next_open) - 1.0
            else:  # next_open_to_close
                next_close = float(df.loc[next_date, "close"])
                label = (next_close / next_open) - 1.0

            features = {
                col: float(feat_df.loc[current_date, col])
                for col in feat_cols
            }

            all_features.append(features)
            all_labels.append(label)
            all_sample_dates.append(current_date)
            all_symbols.append(sym)

    if not all_features:
        return None

    print(f"  Built {len(all_features)} training samples from {len(set(all_symbols))} assets")

    X = pd.DataFrame(all_features, columns=feat_cols)
    X = preprocess_training_features(
        X, all_sample_dates,
        feature_names=feat_cols,
        cross_sectional_scaling=bool(cfg.get("cross_sectional_feature_scaling", True)),
    )
    y = pd.Series(all_labels, dtype=float)

    return X, y, all_sample_dates, all_symbols


# ── Model training functions ──


def _resolve_lightgbm_config(params: dict | None = None) -> dict:
    """Build LightGBM parameter dict from strategy params."""
    cfg = dict(DEFAULT_STRATEGY_PARAMS)
    if params:
        cfg.update(params)
    p = cfg
    return {
        "boosting_type": "gbdt",
        "num_leaves": int(p.get("lightgbm_num_leaves", 15)),
        "max_depth": int(p.get("lightgbm_max_depth", 4)),
        "min_data_in_leaf": int(p.get("lightgbm_min_data_in_leaf", 250)),
        "learning_rate": float(p.get("lightgbm_learning_rate", 0.02)),
        "feature_fraction": float(p.get("lightgbm_feature_fraction", 0.6)),
        "bagging_fraction": float(p.get("lightgbm_bagging_fraction", 0.6)),
        "bagging_freq": int(p.get("lightgbm_bagging_freq", 5)),
        "lambda_l1": float(p.get("lightgbm_lambda_l1", 10.0)),
        "lambda_l2": float(p.get("lightgbm_lambda_l2", 20.0)),
        "min_gain_to_split": float(p.get("lightgbm_min_gain_to_split", 0.1)),
        "verbose": -1,
        "force_col_wise": True,
        "random_state": 42,
        "deterministic": True,
    }


def train_linear_rank(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    train_sample_dates: list[pd.Timestamp],
    X_val: pd.DataFrame | None = None,
    y_val: pd.Series | None = None,
    val_sample_dates: list[pd.Timestamp] | None = None,
    params: dict | None = None,
) -> tuple[LinearRankModel, dict]:
    """Train a LinearRankModel (ridge regression on cross-sectional targets).

    The target is cross-sectionally demeaned and rescaled per date, so the
    model learns relative ranking rather than absolute return prediction.
    Ridge alpha regularizes the coefficients.

    Args:
        X_train: Training feature matrix.
        y_train: Training labels (raw returns).
        train_sample_dates: Date per training sample.
        X_val: Optional validation features.
        y_val: Optional validation labels.
        val_sample_dates: Dates for validation samples.
        params: Strategy parameters.

    Returns:
        (trained_model, score_summary_dict)
    """
    cfg = dict(DEFAULT_STRATEGY_PARAMS)
    if params:
        cfg.update(params)

    alpha = float(cfg.get("linear_rank_alpha", 10.0))

    # Cross-sectionally normalize targets
    y_train_xs = transform_cross_sectional_target(y_train, train_sample_dates)

    # Ridge regression via normal equations
    X_values = X_train.to_numpy(dtype=float)
    feat_mean = X_values.mean(axis=0)
    X_centered = X_values - feat_mean
    y_mean = float(y_train_xs.mean())
    y_centered = y_train_xs.to_numpy(dtype=float) - y_mean

    gram = X_centered.T @ X_centered
    ridge = alpha * np.eye(gram.shape[0], dtype=float)
    coefficients = np.linalg.solve(gram + ridge, X_centered.T @ y_centered)
    intercept = float(y_mean - feat_mean @ coefficients)

    model = LinearRankModel(
        feature_names=list(X_train.columns),
        coefficients=coefficients,
        intercept=intercept,
        ridge_alpha=alpha,
    )

    # Compute scores
    train_preds = model.predict(X_train)
    score_summary = {
        "train": {
            "mean_rank_ic": compute_daily_rank_ic(
                train_preds, y_train, train_sample_dates
            )["mean_ic"],
        }
    }

    if X_val is not None and y_val is not None and val_sample_dates is not None and len(X_val) > 0:
        val_preds = model.predict(X_val)
        score_summary["valid"] = {
            "mean_rank_ic": compute_daily_rank_ic(
                val_preds, y_val, val_sample_dates
            )["mean_ic"],
        }

    return model, score_summary


def train_lightgbm_regression(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame | None = None,
    y_val: pd.Series | None = None,
    params: dict | None = None,
) -> tuple[object, dict]:
    """Train a LightGBM regression model.

    Requires: pip install lightgbm

    Args:
        X_train, y_train: Training data.
        X_val, y_val: Optional validation data for early stopping.
        params: Strategy parameters.

    Returns:
        (lightgbm.Booster, best_score_dict)
    """
    if not _HAS_LIGHTGBM:
        raise ImportError(
            "lightgbm is required for lightgbm_regression. "
            "Install with: uv pip install lightgbm"
        )

    cfg = dict(DEFAULT_STRATEGY_PARAMS)
    if params:
        cfg.update(params)

    lgb_params = _resolve_lightgbm_config(cfg)
    lgb_params.update({"objective": "regression", "metric": "rmse"})

    train_data = lgb.Dataset(X_train, label=y_train)
    valid_sets = [train_data]
    valid_names = ["train"]
    callbacks = []

    if X_val is not None and y_val is not None:
        val_data = lgb.Dataset(X_val, label=y_val)
        valid_sets.append(val_data)
        valid_names.append("valid")
        callbacks.append(
            lgb.early_stopping(
                stopping_rounds=int(cfg.get("lightgbm_early_stopping_rounds", 30))
            )
        )

    model = lgb.train(
        lgb_params,
        train_data,
        num_boost_round=int(cfg.get("lightgbm_num_boost_round", 300)),
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=callbacks,
    )

    return model, getattr(model, "best_score", {})


def train_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    train_sample_dates: list[pd.Timestamp],
    X_val: pd.DataFrame | None = None,
    y_val: pd.Series | None = None,
    val_sample_dates: list[pd.Timestamp] | None = None,
    params: dict | None = None,
) -> tuple[object, dict]:
    """Train a strategy model based on params['model_type'].

    Supported types:
      - linear_rank: Ridge regression (fast, interpretable)
      - lightgbm_regression: Gradient boosting (needs lightgbm installed)
      - lightgbm_ranker: LambdaRank (needs lightgbm installed) — NOT YET IMPLEMENTED

    Args:
        X_train, y_train: Training data.
        train_sample_dates: Date per training sample.
        X_val, y_val: Validation data (optional).
        val_sample_dates: Dates for validation samples.
        params: Strategy parameters.

    Returns:
        (trained_model, score_summary_dict)
    """
    cfg = dict(DEFAULT_STRATEGY_PARAMS)
    if params:
        cfg.update(params)

    model_type = str(cfg.get("model_type", "linear_rank"))

    if model_type == "linear_rank":
        return train_linear_rank(
            X_train, y_train, train_sample_dates,
            X_val=X_val, y_val=y_val, val_sample_dates=val_sample_dates,
            params=cfg,
        )

    if model_type in ("lightgbm_regression", "lightgbm_ranker"):
        if model_type == "lightgbm_ranker":
            raise NotImplementedError(
                "lightgbm_ranker not yet implemented for hot-sector-screener. "
                "Use linear_rank or lightgbm_regression."
            )
        return train_lightgbm_regression(
            X_train, y_train,
            X_val=X_val, y_val=y_val,
            params=cfg,
        )

    raise ValueError(f"Unknown model_type: {model_type}")


def compute_feature_importance(
    model: object,
    feature_names: list[str],
    model_type: str = "linear_rank",
) -> pd.DataFrame:
    """Compute feature importance from a trained model.

    Args:
        model: Trained model object.
        feature_names: List of feature column names.
        model_type: "linear_rank" or "lightgbm_regression".

    Returns:
        DataFrame with columns: feature, importance, method.
    """
    if model_type in ("lightgbm_regression", "lightgbm_ranker"):
        if not hasattr(model, "feature_importance"):
            return pd.DataFrame(columns=["feature", "importance", "method"])
        importances = model.feature_importance(importance_type="gain")
        model_feat_names = (
            model.feature_name() if hasattr(model, "feature_name") else feature_names
        )
        return pd.DataFrame({
            "feature": model_feat_names,
            "importance": importances,
            "method": "gain",
        }).sort_values("importance", ascending=False)

    # Linear model: absolute coefficient magnitude
    coef = np.abs(
        np.asarray(getattr(model, "coefficients", np.zeros(len(feature_names))), dtype=float)
    )
    return pd.DataFrame({
        "feature": feature_names,
        "importance": coef,
        "method": "abs_coef",
    }).sort_values("importance", ascending=False)
