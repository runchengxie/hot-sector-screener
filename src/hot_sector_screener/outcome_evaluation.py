from __future__ import annotations

import math
from typing import Any, cast

import pandas as pd

from .stock_mapper import _normalize_ts_code


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns or frame.empty:
        return pd.Series([], dtype="float64")
    values = cast(pd.Series, pd.to_numeric(frame[column], errors="coerce"))
    return pd.Series(values, index=frame.index, dtype="float64")


def _prepare_daily(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "ts_code" not in frame.columns:
        return pd.DataFrame()
    out = frame.copy()
    out["ts_code"] = out["ts_code"].map(lambda code: _normalize_ts_code(str(code)))
    for column in ("close", "high"):
        if column in out.columns:
            out[column] = _numeric_series(out, column)
    return out.drop_duplicates("ts_code", keep="last").set_index("ts_code")


def _summary(values: pd.Series) -> dict[str, Any]:
    clean = pd.Series(pd.to_numeric(values, errors="coerce"), dtype="float64").dropna()
    if clean.empty:
        return {"available": False, "count": 0}
    return {
        "available": True,
        "count": len(clean),
        "mean_pct": round(float(clean.mean() * 100), 2),
        "median_pct": round(float(clean.median() * 100), 2),
        "hit_rate_pct": round(float((clean > 0).mean() * 100), 1),
        "top_pct": round(float(clean.max() * 100), 2),
        "bottom_pct": round(float(clean.min() * 100), 2),
    }


def _float_by_index(series: pd.Series) -> dict[str, float]:
    values: dict[str, float] = {}
    for index, value in series.dropna().items():
        number = float(value)
        if math.isfinite(number):
            values[str(index)] = number
    return values


def build_candidate_outcome_report(
    candidates: list[dict[str, Any]],
    base_daily: pd.DataFrame,
    future_daily_sequence: list[pd.DataFrame],
    *,
    horizons: tuple[int, ...] = (1, 3),
) -> dict[str, Any]:
    """Evaluate T+1/T+3 next-high and close outcomes when future bars exist."""
    if not candidates:
        return {"available": False, "reason": "empty_candidate_universe", "horizons": {}}
    base = _prepare_daily(base_daily)
    if base.empty or "close" not in base.columns:
        return {"available": False, "reason": "base_daily_unavailable", "horizons": {}}
    codes = [str(item.get("ts_code", "")) for item in candidates if item.get("ts_code")]
    base = base.loc[base.index.intersection(codes)]
    if base.empty:
        return {"available": False, "reason": "base_prices_missing", "horizons": {}}
    base_prices = {
        code: value
        for code, value in _float_by_index(_numeric_series(base, "close")).items()
        if value > 0
    }
    if not base_prices:
        return {"available": False, "reason": "base_prices_invalid", "horizons": {}}

    prepared_future = [_prepare_daily(frame) for frame in future_daily_sequence]
    horizons_out: dict[str, Any] = {}
    for horizon in horizons:
        if horizon <= 0 or len(prepared_future) < horizon:
            horizons_out[f"t_plus_{horizon}"] = {
                "available": False,
                "reason": "future_daily_unavailable",
            }
            continue
        frames = [frame for frame in prepared_future[:horizon] if not frame.empty]
        if len(frames) < horizon:
            horizons_out[f"t_plus_{horizon}"] = {
                "available": False,
                "reason": "future_daily_unavailable",
            }
            continue

        high_parts = [_numeric_series(frame, "high") for frame in frames if "high" in frame.columns]
        close_frame = frames[-1]
        if not high_parts or "close" not in close_frame.columns:
            horizons_out[f"t_plus_{horizon}"] = {
                "available": False,
                "reason": "future_prices_missing",
            }
            continue
        future_high_by_code: dict[str, float] = {}
        for part in high_parts:
            for code, high_value in _float_by_index(part).items():
                future_high_by_code[code] = max(
                    future_high_by_code.get(code, high_value), high_value
                )
        future_close_by_code = _float_by_index(_numeric_series(close_frame, "close"))
        next_high_values: list[float] = []
        close_values: list[float] = []
        for code, base_value in base_prices.items():
            if code not in future_high_by_code or code not in future_close_by_code:
                continue
            next_high_values.append(future_high_by_code[code] / base_value - 1.0)
            close_values.append(future_close_by_code[code] / base_value - 1.0)
        if not next_high_values or not close_values:
            horizons_out[f"t_plus_{horizon}"] = {
                "available": False,
                "reason": "no_price_overlap",
            }
            continue
        next_high_return = pd.Series(next_high_values, dtype="float64")
        close_return = pd.Series(close_values, dtype="float64")
        horizons_out[f"t_plus_{horizon}"] = {
            "available": True,
            "next_high_return": _summary(next_high_return),
            "close_return": _summary(close_return),
        }

    return {
        "available": any(item.get("available") for item in horizons_out.values()),
        "horizons": horizons_out,
    }
