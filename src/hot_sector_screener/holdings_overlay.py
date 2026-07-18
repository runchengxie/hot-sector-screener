from __future__ import annotations

import math
from datetime import datetime
from typing import Any, cast

import pandas as pd

from .daily_confirmation import build_daily_confirmation
from .holdings_contract import (
    HOLDINGS_OVERLAY_ARTIFACT_TYPE,
    HOLDINGS_OVERLAY_SCHEMA_VERSION,
    TECHNICAL_FIELDS,
    HoldingsOverlayContractError,
    canonical_sha256,
    holdings_feature_policy,
    validate_holdings_overlay,
    validate_holdings_snapshot,
)
from .observation_time import date_key
from .stock_mapper import _is_st_name, _normalize_ts_code, _safe_float


def _current_daily_rows(
    daily_df: pd.DataFrame,
    *,
    observation_date: str,
) -> dict[str, dict[str, Any]]:
    if daily_df.empty or "ts_code" not in daily_df.columns:
        return {}
    frame = daily_df.copy()
    frame["ts_code"] = frame["ts_code"].map(lambda value: _normalize_ts_code(str(value)))
    if "trade_date" in frame.columns:
        dates = frame["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
        frame = frame.loc[dates.eq(observation_date)].copy()
    for column in ("amount", "close", "high", "low", "pct_chg"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "amount" in frame.columns:
        frame["amount_rank_pct"] = frame["amount"].rank(pct=True, method="average") * 100
    else:
        frame["amount_rank_pct"] = math.nan
    frame = frame.loc[frame["ts_code"].astype(bool)].drop_duplicates("ts_code", keep="last")
    records = cast(list[dict[str, Any]], frame.to_dict(orient="records"))
    return {str(record["ts_code"]): record for record in records}


def _valid_date_or_empty(value: object) -> str:
    try:
        return date_key(value)
    except ValueError:
        return ""


def _technical_rows(
    daily_history: pd.DataFrame,
    *,
    observation_date: str,
) -> dict[str, dict[str, Any]]:
    if daily_history.empty or "ts_code" not in daily_history.columns:
        return {}
    frame = daily_history.copy()
    date_column = "_date_int" if "_date_int" in frame.columns else "trade_date"
    if date_column not in frame.columns:
        return {}
    frame["ts_code"] = frame["ts_code"].map(lambda value: _normalize_ts_code(str(value)))
    frame["_overlay_date"] = frame[date_column].map(_valid_date_or_empty)
    frame = frame.loc[frame["_overlay_date"].astype(bool)].copy()
    if frame.empty:
        return {}
    latest = frame.groupby("ts_code", sort=False)["_overlay_date"].max().to_dict()
    features = build_daily_confirmation(frame)
    if features.empty:
        return {}
    records = cast(list[dict[str, Any]], features.to_dict(orient="records"))
    return {
        str(record["ts_code"]): record
        for record in records
        if latest.get(str(record["ts_code"])) == observation_date
    }


def _hard_market_gate(
    record: dict[str, Any] | None,
    *,
    fallback_name: str,
    min_price: float,
    max_price: float,
    allow_st: bool,
) -> tuple[bool, list[str], dict[str, Any]]:
    if record is None:
        return (
            False,
            ["current_daily_unavailable"],
            {
                "close": None,
                "amount_rank_pct": None,
                "liquidity_score": None,
            },
        )
    name = str(record.get("name") or record.get("ts_name") or fallback_name or "")
    close = _safe_float(record.get("close"), math.nan)
    amount = _safe_float(record.get("amount"), math.nan)
    amount_rank_pct = _safe_float(record.get("amount_rank_pct"), math.nan)
    reasons: list[str] = []
    if not math.isfinite(close) or close <= 0:
        reasons.append("current_close_unavailable")
    elif close < min_price or close > max_price:
        reasons.append("price_out_of_range")
    if not math.isfinite(amount) or amount < 0 or not math.isfinite(amount_rank_pct):
        reasons.append("current_liquidity_unavailable")
    if not allow_st and _is_st_name(name):
        reasons.append("st_excluded")
    high = _safe_float(record.get("high"), math.nan)
    low = _safe_float(record.get("low"), math.nan)
    pct_chg = abs(_safe_float(record.get("pct_chg"), 0.0))
    if (
        math.isfinite(high)
        and math.isfinite(low)
        and high > 0
        and low > 0
        and abs(high - low) <= 1e-9
        and pct_chg >= 9.5
    ):
        reasons.append("one_price_limit")
    liquidity_score = (
        round(max(min(amount_rank_pct / 100.0, 1.0), 0.0), 3)
        if math.isfinite(amount_rank_pct)
        else None
    )
    return (
        not reasons,
        reasons,
        {
            "close": round(close, 2) if math.isfinite(close) else None,
            "amount_rank_pct": (
                round(amount_rank_pct, 1) if math.isfinite(amount_rank_pct) else None
            ),
            "liquidity_score": liquidity_score,
        },
    )


def _overlay_row(
    symbol: str,
    *,
    base: dict[str, Any] | None,
    theme: dict[str, Any] | None,
    is_current_holding: bool,
    observation_date: str,
    daily_record: dict[str, Any] | None,
    technical_record: dict[str, Any] | None,
    min_amount_rank_pct: float,
    min_price: float,
    max_price: float,
    allow_st: bool,
) -> dict[str, Any]:
    current_theme_match = theme is not None
    fallback_name = str((base or {}).get("name") or (theme or {}).get("name") or "")
    hard_eligible, market_reasons, market = _hard_market_gate(
        daily_record,
        fallback_name=fallback_name,
        min_price=min_price,
        max_price=max_price,
        allow_st=allow_st,
    )
    entry_reasons = list(market_reasons)
    if not current_theme_match:
        entry_reasons.append("not_current_theme")
    amount_rank_pct = market["amount_rank_pct"]
    if (
        hard_eligible
        and isinstance(amount_rank_pct, float)
        and amount_rank_pct < min_amount_rank_pct
    ):
        entry_reasons.append("entry_liquidity_below_threshold")
    technical_available = technical_record is not None
    hold_reasons = list(market_reasons)
    if not technical_available:
        hold_reasons.append("current_day_technical_unavailable")
    source = theme or {}
    row: dict[str, Any] = {
        "ts_code": symbol,
        "name": str(
            source.get("name")
            or (base or {}).get("name")
            or (daily_record or {}).get("name")
            or (daily_record or {}).get("ts_name")
            or ""
        ),
        "is_current_holding": is_current_holding,
        "entry_eligible": not entry_reasons,
        "hold_eligible": hard_eligible and technical_available,
        "current_theme_match": current_theme_match,
        "theme_score": (round(_safe_float(source.get("score")), 6) if current_theme_match else 0.0),
        "theme_relevance": (
            round(_safe_float(source.get("relevance")), 6) if current_theme_match else 0.0
        ),
        "source_topics": list(source.get("source_topics") or []) if current_theme_match else [],
        "source_concepts": (
            list(source.get("source_concepts") or []) if current_theme_match else []
        ),
        "last_theme_seen": observation_date if current_theme_match else None,
        "theme_age": 0 if current_theme_match else None,
        "technical_as_of_date": observation_date if technical_available else None,
        "technical_history_days": (
            int(_safe_float((technical_record or {}).get("daily_history_days")))
            if technical_available
            else None
        ),
        **market,
        "entry_ineligible_reasons": sorted(set(entry_reasons)),
        "hold_ineligible_reasons": sorted(set(hold_reasons)),
    }
    for field in TECHNICAL_FIELDS:
        row[field] = (
            _safe_float((technical_record or {}).get(field)) if technical_available else None
        )
    return row


def build_holdings_overlay(
    *,
    candidate_result: dict[str, Any],
    current_theme_candidates: list[dict[str, Any]],
    holdings_snapshot: object,
    daily_df: pd.DataFrame,
    daily_history: pd.DataFrame,
    min_amount_rank_pct: float,
    min_price: float,
    max_price: float,
    allow_st: bool,
) -> dict[str, Any]:
    """Build a non-portfolio companion artifact for current-day incumbent rescoring."""

    observation_date = date_key(candidate_result.get("observation_date"))
    snapshot = validate_holdings_snapshot(
        holdings_snapshot,
        observation_date=observation_date,
    )
    base_candidates = cast(list[dict[str, Any]], candidate_result.get("candidate_universe") or [])
    base_by_code = {str(row.get("ts_code")): row for row in base_candidates}
    theme_by_code = {str(row.get("ts_code")): row for row in current_theme_candidates}
    holding_symbols = cast(list[str], snapshot["symbols"])
    symbols = list(base_by_code)
    symbols.extend(symbol for symbol in holding_symbols if symbol not in base_by_code)
    daily_by_code = _current_daily_rows(daily_df, observation_date=observation_date)
    technical_by_code = _technical_rows(daily_history, observation_date=observation_date)
    holding_set = set(holding_symbols)
    rows = [
        _overlay_row(
            symbol,
            base=base_by_code.get(symbol),
            theme=theme_by_code.get(symbol),
            is_current_holding=symbol in holding_set,
            observation_date=observation_date,
            daily_record=daily_by_code.get(symbol),
            technical_record=technical_by_code.get(symbol),
            min_amount_rank_pct=float(min_amount_rank_pct),
            min_price=float(min_price),
            max_price=float(max_price),
            allow_st=bool(allow_st),
        )
        for symbol in symbols
    ]
    generated_at = str(candidate_result.get("generated_at") or "")
    try:
        datetime.fromisoformat(generated_at)
    except ValueError as exc:
        raise HoldingsOverlayContractError(
            "candidate generated_at must be an ISO-8601 timestamp"
        ) from exc
    result: dict[str, Any] = {
        "schema_version": HOLDINGS_OVERLAY_SCHEMA_VERSION,
        "artifact_type": HOLDINGS_OVERLAY_ARTIFACT_TYPE,
        "feature_policy": holdings_feature_policy(),
        "market": "CN",
        "observation_date": observation_date,
        "data_cutoff": observation_date,
        "execution_not_before": "next_trading_session",
        "future_data_included": False,
        "strict_point_in_time": False,
        "generated_at": generated_at,
        "candidate_artifact_type": candidate_result.get("artifact_type"),
        "candidate_schema_version": candidate_result.get("schema_version"),
        "candidate_payload_sha256": canonical_sha256(candidate_result),
        "eligibility_parameters": {
            "entry_min_amount_rank_pct": float(min_amount_rank_pct),
            "min_price": float(min_price),
            "max_price": float(max_price),
            "allow_st": bool(allow_st),
        },
        "holdings_snapshot": {
            "schema_version": snapshot["schema_version"],
            "artifact_type": snapshot["artifact_type"],
            "as_of_date": snapshot["as_of_date"],
            "sha256": canonical_sha256(snapshot),
            "symbol_count": len(holding_symbols),
        },
        "rows": rows,
        "row_count": len(rows),
    }
    return validate_holdings_overlay(result)
