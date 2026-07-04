from __future__ import annotations

import math
from typing import Any, cast

import pandas as pd

from .data_sources.platform import list_available_dates, load_daily_data
from .stock_mapper import _normalize_ts_code, _safe_float


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if not math.isfinite(value):
        return low
    return max(low, min(high, value))


def _date_int(value: str) -> str:
    return str(value).replace("-", "")[:8]


def load_daily_history(as_of_date: str, *, lookback: int = 20) -> pd.DataFrame:
    """Load daily bars up to as_of_date for optional technical confirmation."""
    as_of = _date_int(as_of_date)
    try:
        dates = [date for date in list_available_dates("daily") if date <= as_of]
    except RuntimeError:
        return pd.DataFrame()
    if not dates:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for trade_date in dates[-max(lookback, 1) :]:
        frame = load_daily_data(trade_date)
        if frame.empty or "ts_code" not in frame.columns:
            continue
        item = frame.copy()
        item["trade_date"] = item.get("trade_date", trade_date)
        item["_date_int"] = trade_date
        item["ts_code"] = item["ts_code"].map(lambda code: _normalize_ts_code(str(code)))
        frames.append(item)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _numeric_series(rows: pd.DataFrame, column: str) -> pd.Series:
    if column not in rows.columns or rows.empty:
        return pd.Series([], dtype="float64")
    values = cast(pd.Series, pd.to_numeric(rows[column], errors="coerce"))
    return pd.Series(values, index=rows.index, dtype="float64")


def _last_numeric(series: pd.Series, default: float = 0.0) -> float:
    clean = series.dropna()
    if clean.empty:
        return default
    return _safe_float(clean.iloc[-1], default)


def build_daily_confirmation(
    daily_history: pd.DataFrame,
    *,
    min_history: int = 3,
) -> pd.DataFrame:
    """Build bounded daily technical confirmation scores by stock."""
    if daily_history.empty or "ts_code" not in daily_history.columns:
        return pd.DataFrame()

    frame = daily_history.copy()
    if "_date_int" not in frame.columns:
        frame["_date_int"] = frame.get("trade_date", "")
    frame["_date_int"] = frame["_date_int"].astype(str).str.replace("-", "", regex=False)
    frame = frame.sort_values(["ts_code", "_date_int"])

    rows: list[dict[str, Any]] = []
    for code, group in frame.groupby("ts_code", sort=False):
        group = group.tail(20).copy()
        if len(group) < min_history:
            continue

        close = _numeric_series(group, "close")
        high = _numeric_series(group, "high")
        low = _numeric_series(group, "low")
        amount = _numeric_series(group, "amount")
        pct_chg = _numeric_series(group, "pct_chg")
        clean_close = close.dropna()
        if clean_close.empty:
            continue

        latest_close = _last_numeric(clean_close)
        if latest_close <= 0:
            continue
        first_close = _safe_float(clean_close.iloc[0], latest_close)
        prev_5d_close = _safe_float(clean_close.iloc[-6]) if len(clean_close) >= 6 else first_close
        prev_10d_close = (
            _safe_float(clean_close.iloc[-11]) if len(clean_close) >= 11 else prev_5d_close
        )
        ret_5d = float(latest_close / prev_5d_close - 1.0) if prev_5d_close > 0 else 0.0
        ret_10d = float(latest_close / prev_10d_close - 1.0) if prev_10d_close > 0 else ret_5d

        high_window = high.dropna().tail(20)
        close_to_20d_high = (
            float(latest_close / high_window.max()) if not high_window.empty else 0.5
        )
        ma_window = close.dropna().tail(20)
        ma_gap = float(latest_close / ma_window.mean() - 1.0) if not ma_window.empty else 0.0

        amount_window = amount.dropna().tail(20)
        latest_amount = _last_numeric(amount)
        amount_median = float(amount_window.median()) if not amount_window.empty else 0.0
        amount_ratio_20d = latest_amount / amount_median if amount_median > 0 else 1.0

        latest_high = _last_numeric(high, latest_close)
        latest_low = _last_numeric(low, latest_close)
        range_pct = max(latest_high - latest_low, 0.0) / latest_close

        momentum_score = _clip((ret_5d + 0.05) / 0.15)
        position_score = _clip(close_to_20d_high)
        ma_score = _clip((ma_gap + 0.05) / 0.12)
        volume_score = _clip((amount_ratio_20d - 0.6) / 1.4)
        risk_score = 1.0 - _clip((range_pct - 0.03) / 0.12)
        overheat_penalty = 0.12 if ret_5d > 0.18 or _last_numeric(pct_chg) > 9.5 else 0.0

        trend_score = _clip(0.5 * momentum_score + 0.3 * position_score + 0.2 * ma_score)
        daily_confirm_score = _clip(
            0.45 * trend_score
            + 0.25 * volume_score
            + 0.20 * risk_score
            + 0.10 * position_score
            - overheat_penalty
        )

        rows.append(
            {
                "ts_code": str(code),
                "daily_confirm_score": round(daily_confirm_score, 4),
                "trend_score": round(trend_score, 4),
                "volume_score": round(volume_score, 4),
                "risk_score": round(risk_score, 4),
                "ret_5d": round(ret_5d, 6),
                "ret_10d": round(ret_10d, 6),
                "close_to_20d_high": round(close_to_20d_high, 4),
                "amount_ratio_20d": round(amount_ratio_20d, 4),
                "daily_history_days": len(group),
            }
        )
    return pd.DataFrame(rows)


def apply_daily_confirmation_overlay(
    stocks: list[dict[str, Any]],
    daily_history: pd.DataFrame,
    *,
    weight: float = 0.2,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """Nudge candidate scores with daily technical confirmation when available."""
    if not stocks:
        return []
    features = build_daily_confirmation(daily_history)
    if features.empty:
        return stocks
    records = cast(list[dict[str, Any]], features.to_dict(orient="records"))
    by_code = {str(record.get("ts_code", "")): record for record in records}
    bounded_weight = _clip(float(weight))
    out: list[dict[str, Any]] = []
    for stock in stocks:
        item = dict(stock)
        code = _normalize_ts_code(str(item.get("ts_code", "")))
        record = by_code.get(code)
        if record is None:
            out.append(item)
            continue
        daily_score = _safe_float(record.get("daily_confirm_score"), 0.5)
        if min_score is not None and daily_score < float(min_score):
            continue
        multiplier = 1.0 + bounded_weight * ((daily_score - 0.5) * 2.0)
        base_score = _safe_float(item.get("score"), _safe_float(item.get("relevance"), 0.0))
        item.update(
            {
                "ts_code": code,
                "daily_confirm_score": daily_score,
                "trend_score": _safe_float(record.get("trend_score")),
                "volume_score": _safe_float(record.get("volume_score")),
                "risk_score": _safe_float(record.get("risk_score")),
                "ret_5d": _safe_float(record.get("ret_5d")),
                "ret_10d": _safe_float(record.get("ret_10d")),
                "close_to_20d_high": _safe_float(record.get("close_to_20d_high")),
                "amount_ratio_20d": _safe_float(record.get("amount_ratio_20d")),
                "daily_history_days": int(_safe_float(record.get("daily_history_days"))),
                "pre_daily_score": round(base_score, 6),
                "daily_score_multiplier": round(multiplier, 4),
                "score": round(base_score * multiplier, 6),
            }
        )
        out.append(item)

    max_score = max((_safe_float(item.get("score")) for item in out), default=0.0)
    if max_score > 0:
        for item in out:
            item["relevance"] = round(min(_safe_float(item.get("score")) / max_score, 1.0), 3)
    return sorted(out, key=lambda item: (-_safe_float(item.get("score")), str(item.get("ts_code"))))
