from __future__ import annotations

from typing import Any

import pandas as pd

from .stock_mapper import _normalize_ts_code, _safe_float

POSITIVE_HOTSPOT_FEATURE_WEIGHTS: dict[str, float] = {
    "hot_rank_pct": 1.0,
    "hot_zscore": 1.0,
    "rank_change": 0.35,
    "theme_strength_z": 0.85,
    "theme_hot_z": 0.7,
    "theme_limit_up_count": 0.75,
    "strong_theme_count": 0.75,
    "max_theme_strength": 0.65,
    "is_theme_leader": 0.8,
    "kpl_theme_count": 0.45,
    "kpl_theme_hot_num_max": 0.55,
    "kpl_limit_up_count_5d": 0.55,
    "limit_step_max": 0.35,
    "report_rc_count_20d": 0.25,
    "rating_buy_count_20d": 0.25,
    "survey_count_20d": 0.2,
    "broker_recommend_count": 0.25,
}

LOWER_IS_BETTER_HOTSPOT_FEATURE_WEIGHTS: dict[str, float] = {
    "days_since_hot": 0.65,
    "failed_board_count_5d": 0.45,
}

FEATURE_CODE_COLUMNS = ("ts_code", "symbol", "code")


def _rank_pct(series: pd.Series, *, higher_is_better: bool = True) -> pd.Series:
    values = pd.Series(pd.to_numeric(series, errors="coerce"), index=series.index)
    ranked = values.rank(pct=True, method="average", ascending=True)
    if not higher_is_better:
        ranked = 1.0 - ranked
    return ranked.fillna(0.0).clip(0.0, 1.0)


def _resolve_feature_code_column(frame: pd.DataFrame) -> str | None:
    for column in FEATURE_CODE_COLUMNS:
        if column in frame.columns:
            return column
    return None


def _compute_hotspot_feature_scores(features: pd.DataFrame) -> pd.DataFrame:
    if features is None or features.empty:
        return pd.DataFrame(columns=["ts_code", "hotspot_feature_score"])

    code_column = _resolve_feature_code_column(features)
    if code_column is None:
        return pd.DataFrame(columns=["ts_code", "hotspot_feature_score"])

    frame = features.copy()
    frame["ts_code"] = frame[code_column].map(lambda code: _normalize_ts_code(str(code)))
    frame = frame[frame["ts_code"].astype(bool)].copy()
    if frame.empty:
        return pd.DataFrame(columns=["ts_code", "hotspot_feature_score"])

    weighted_components: list[pd.Series] = []
    weights: list[float] = []

    for column, weight in POSITIVE_HOTSPOT_FEATURE_WEIGHTS.items():
        if column in frame.columns:
            weighted_components.append(_rank_pct(frame[column], higher_is_better=True) * weight)
            weights.append(weight)

    for column, weight in LOWER_IS_BETTER_HOTSPOT_FEATURE_WEIGHTS.items():
        if column in frame.columns:
            weighted_components.append(_rank_pct(frame[column], higher_is_better=False) * weight)
            weights.append(weight)

    if not weighted_components or not weights:
        return pd.DataFrame(columns=["ts_code", "hotspot_feature_score"])

    total_weight = sum(weights)
    score = pd.concat(weighted_components, axis=1).sum(axis=1) / total_weight
    out = pd.DataFrame(
        {
            "ts_code": frame["ts_code"],
            "hotspot_feature_score": score.fillna(0.0).clip(0.0, 1.0),
        }
    )
    return (
        out.sort_values("hotspot_feature_score", ascending=False)
        .drop_duplicates("ts_code", keep="first")
        .reset_index(drop=True)
    )


def apply_hotspot_feature_overlay(
    stocks: list[dict[str, Any]],
    hotspot_features: pd.DataFrame | None,
    *,
    weight: float = 0.25,
    min_multiplier: float = 0.85,
    max_multiplier: float = 1.25,
) -> list[dict[str, Any]]:
    """Blend derived hotspot features into deterministic topic scores.

    The topic mapper remains the primary signal source.  The feature overlay only
    nudges candidates that have independent evidence from hotspot-derived stock
    features, with a bounded multiplier to avoid turning this into an opaque model.
    """
    if not stocks:
        return []
    if hotspot_features is None or hotspot_features.empty or weight <= 0:
        return stocks

    feature_scores = _compute_hotspot_feature_scores(hotspot_features)
    if feature_scores.empty:
        return stocks

    score_by_code = {
        str(code): float(score)
        for code, score in zip(
            feature_scores["ts_code"],
            feature_scores["hotspot_feature_score"],
            strict=False,
        )
    }
    bounded_weight = max(0.0, min(float(weight), 1.0))
    spread = max_multiplier - min_multiplier
    out: list[dict[str, Any]] = []
    changed = False
    for stock in stocks:
        item = dict(stock)
        code = _normalize_ts_code(str(item.get("ts_code", "")))
        feature_score = score_by_code.get(code)
        if feature_score is None:
            out.append(item)
            continue

        centered_multiplier = min_multiplier + spread * max(0.0, min(feature_score, 1.0))
        multiplier = 1.0 + bounded_weight * (centered_multiplier - 1.0)
        base_score = _safe_float(item.get("score"), _safe_float(item.get("relevance"), 0.0))
        base_relevance = _safe_float(item.get("relevance"), 0.0)

        item["pre_hotspot_score"] = round(base_score, 6)
        item["hotspot_feature_score"] = round(feature_score, 4)
        item["hotspot_score_multiplier"] = round(multiplier, 4)
        item["score"] = round(base_score * multiplier, 6)
        item["relevance"] = round(min(base_relevance * multiplier, 1.0), 6)
        changed = True
        out.append(item)

    if not changed:
        return stocks

    max_score = max((_safe_float(item.get("score")) for item in out), default=0.0)
    if max_score > 0:
        for item in out:
            item["relevance"] = round(min(_safe_float(item.get("score")) / max_score, 1.0), 3)

    return sorted(out, key=lambda item: (-_safe_float(item.get("score")), str(item.get("ts_code"))))
