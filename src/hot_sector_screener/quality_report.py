from __future__ import annotations

from typing import Any

import pandas as pd


def build_candidate_quality_report(
    candidates: list[dict[str, Any]],
    base_daily: pd.DataFrame,
    future_daily_by_horizon: dict[int, pd.DataFrame],
) -> dict[str, Any]:
    """Summarise available T+1/T+3/T+5 close-to-close candidate performance."""
    if not candidates:
        return {"available": False, "reason": "empty_candidate_universe", "horizons": {}}
    if base_daily.empty or "ts_code" not in base_daily.columns or "close" not in base_daily.columns:
        return {"available": False, "reason": "base_daily_unavailable", "horizons": {}}

    candidate_codes = [str(item.get("ts_code", "")) for item in candidates if item.get("ts_code")]
    base = base_daily[base_daily["ts_code"].isin(candidate_codes)].copy()
    if base.empty:
        return {"available": False, "reason": "base_prices_missing", "horizons": {}}

    base["close"] = pd.to_numeric(base["close"], errors="coerce")
    base_prices = base.dropna(subset=["close"]).set_index("ts_code")["close"]
    if base_prices.empty:
        return {"available": False, "reason": "base_prices_invalid", "horizons": {}}

    horizons: dict[str, Any] = {}
    for horizon, future_daily in sorted(future_daily_by_horizon.items()):
        label = f"t_plus_{horizon}"
        if (
            future_daily.empty
            or "ts_code" not in future_daily.columns
            or "close" not in future_daily.columns
        ):
            horizons[label] = {"available": False, "reason": "future_daily_unavailable"}
            continue

        future = future_daily[future_daily["ts_code"].isin(base_prices.index)].copy()
        future["close"] = pd.to_numeric(future["close"], errors="coerce")
        future_prices = future.dropna(subset=["close"]).set_index("ts_code")["close"]
        aligned = pd.concat(
            [base_prices.rename("base"), future_prices.rename("future")],
            axis=1,
        ).dropna()
        aligned = aligned[aligned["base"] > 0]
        if aligned.empty:
            horizons[label] = {"available": False, "reason": "no_price_overlap"}
            continue

        return_values = [
            float(row.future) / float(row.base) - 1.0 for row in aligned.itertuples(index=False)
        ]
        returns = pd.Series(return_values, dtype="float64")
        horizons[label] = {
            "available": True,
            "count": len(returns),
            "mean_return_pct": round(float(returns.mean() * 100), 2),
            "median_return_pct": round(float(returns.median() * 100), 2),
            "hit_rate_pct": round(float((returns > 0).mean() * 100), 1),
            "top_return_pct": round(float(returns.max() * 100), 2),
            "bottom_return_pct": round(float(returns.min() * 100), 2),
        }

    return {
        "available": any(item.get("available") for item in horizons.values()),
        "horizons": horizons,
    }
