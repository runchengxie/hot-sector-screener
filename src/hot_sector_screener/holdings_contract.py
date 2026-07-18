from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from .observation_time import date_key

HOLDINGS_SNAPSHOT_SCHEMA_VERSION = "1.0.0"
HOLDINGS_SNAPSHOT_ARTIFACT_TYPE = "hot_sector_holdings_snapshot"
HOLDINGS_OVERLAY_SCHEMA_VERSION = "1.0.0"
HOLDINGS_OVERLAY_ARTIFACT_TYPE = "hot_sector_holdings_eligibility_overlay"
HOLDINGS_OVERLAY_FILE_NAME = "holdings_eligibility_overlay.json"
HOLDINGS_FEATURE_POLICY_ID = "hotsector.holdings_overlay.daily_rescore"
HOLDINGS_FEATURE_POLICY_VERSION = "1.0.0"

TECHNICAL_FIELDS = (
    "daily_confirm_score",
    "trend_score",
    "volume_score",
    "risk_score",
    "ret_5d",
    "ret_10d",
    "close_to_20d_high",
    "amount_ratio_20d",
)

_SYMBOL_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OVERLAY_FIELDS = {
    "schema_version",
    "artifact_type",
    "feature_policy",
    "market",
    "observation_date",
    "data_cutoff",
    "execution_not_before",
    "future_data_included",
    "strict_point_in_time",
    "generated_at",
    "candidate_artifact_type",
    "candidate_schema_version",
    "candidate_payload_sha256",
    "eligibility_parameters",
    "holdings_snapshot",
    "rows",
    "row_count",
}
_ROW_FIELDS = {
    "ts_code",
    "name",
    "is_current_holding",
    "entry_eligible",
    "hold_eligible",
    "current_theme_match",
    "theme_score",
    "theme_relevance",
    "source_topics",
    "source_concepts",
    "last_theme_seen",
    "theme_age",
    "technical_as_of_date",
    "technical_history_days",
    "close",
    "amount_rank_pct",
    "liquidity_score",
    "entry_ineligible_reasons",
    "hold_ineligible_reasons",
    *TECHNICAL_FIELDS,
}


class HoldingsOverlayContractError(ValueError):
    """Raised when a holdings snapshot or eligibility overlay is unsafe."""


def canonical_sha256(value: object) -> str:
    """Hash a JSON-compatible value with the owner canonical representation."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def holdings_feature_policy() -> dict[str, Any]:
    """Return the immutable producer policy for a current-day holdings overlay."""

    policy: dict[str, Any] = {
        "policy_id": HOLDINGS_FEATURE_POLICY_ID,
        "version": HOLDINGS_FEATURE_POLICY_VERSION,
        "universe_membership": "base_candidates_union_declared_holdings",
        "entry_eligibility": "current_theme_match_and_current_day_entry_market_gate",
        "hold_eligibility": ("current_day_hard_market_gate_and_current_day_technical_features"),
        "hold_liquidity_threshold": None,
        "theme_score_when_no_current_match": 0.0,
        "theme_relevance_when_no_current_match": 0.0,
        "theme_history": {
            "age_unit": "trading_sessions",
            "current_match_last_theme_seen": "observation_date",
            "current_match_theme_age": 0,
            "absent_match_last_theme_seen": None,
            "absent_match_theme_age": None,
            "absent_reason": "verified_theme_history_not_supplied",
        },
        "technical_features": {
            "source": "daily_history_ending_on_observation_date",
            "forward_fill": False,
            "stale_values_allowed": False,
        },
        "liquidity_features": {
            "source": "observation_date_daily_cross_section",
            "forward_fill": False,
            "stale_values_allowed": False,
        },
        "portfolio_selection_owner": "downstream_consumer",
    }
    policy["canonical_sha256"] = canonical_sha256(policy)
    return policy


def holdings_overlay_contract_info() -> dict[str, Any]:
    """Expose the companion input/output contracts without changing candidate v2."""

    return {
        "input": {
            "artifact_type": HOLDINGS_SNAPSHOT_ARTIFACT_TYPE,
            "schema_version": HOLDINGS_SNAPSHOT_SCHEMA_VERSION,
        },
        "output": {
            "artifact_type": HOLDINGS_OVERLAY_ARTIFACT_TYPE,
            "schema_version": HOLDINGS_OVERLAY_SCHEMA_VERSION,
            "file_name": HOLDINGS_OVERLAY_FILE_NAME,
        },
        "feature_policy": holdings_feature_policy(),
    }


def validate_holdings_snapshot(
    payload: object,
    *,
    observation_date: str,
) -> dict[str, Any]:
    """Validate and canonicalize the declared incumbent-symbol snapshot."""

    if not isinstance(payload, dict):
        raise HoldingsOverlayContractError("holdings snapshot root must be an object")
    raw = cast(dict[str, Any], payload)
    expected_fields = {
        "schema_version",
        "artifact_type",
        "market",
        "as_of_date",
        "symbols",
    }
    if set(raw) != expected_fields:
        raise HoldingsOverlayContractError(
            "holdings snapshot must contain exactly the versioned contract fields"
        )
    if raw.get("schema_version") != HOLDINGS_SNAPSHOT_SCHEMA_VERSION:
        raise HoldingsOverlayContractError("holdings snapshot schema_version is invalid")
    if raw.get("artifact_type") != HOLDINGS_SNAPSHOT_ARTIFACT_TYPE:
        raise HoldingsOverlayContractError("holdings snapshot artifact_type is invalid")
    if raw.get("market") != "CN":
        raise HoldingsOverlayContractError("holdings snapshot market must be CN")
    try:
        as_of = date_key(raw.get("as_of_date"))
        observation = date_key(observation_date)
    except ValueError as exc:
        raise HoldingsOverlayContractError(str(exc)) from exc
    if as_of > observation:
        raise HoldingsOverlayContractError(
            "holdings snapshot as_of_date must not follow observation_date"
        )
    values = raw.get("symbols")
    if not isinstance(values, list) or len(values) > 1_000:
        raise HoldingsOverlayContractError(
            "holdings snapshot symbols must be an array with at most 1000 items"
        )
    symbols: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise HoldingsOverlayContractError("holdings snapshot symbols must be strings")
        symbol = value.strip().upper()
        if _SYMBOL_PATTERN.fullmatch(symbol) is None:
            raise HoldingsOverlayContractError(f"holdings snapshot symbol is invalid: {value!r}")
        if symbol in symbols:
            raise HoldingsOverlayContractError(f"holdings snapshot symbol is duplicated: {symbol}")
        symbols.append(symbol)
    return {
        "schema_version": HOLDINGS_SNAPSHOT_SCHEMA_VERSION,
        "artifact_type": HOLDINGS_SNAPSHOT_ARTIFACT_TYPE,
        "market": "CN",
        "as_of_date": as_of,
        "symbols": symbols,
    }


def load_holdings_snapshot(
    path: str | Path,
    *,
    observation_date: str,
) -> dict[str, Any]:
    """Read and validate a holdings snapshot without accepting implicit fields."""

    resolved = Path(path).expanduser()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HoldingsOverlayContractError(
            f"cannot read holdings snapshot: {resolved}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise HoldingsOverlayContractError(f"holdings snapshot is invalid JSON: {exc}") from exc
    return validate_holdings_snapshot(payload, observation_date=observation_date)


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _safe_float(value: object) -> float:
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _eligibility_parameter_issues(value: object) -> list[str]:
    if not isinstance(value, dict) or set(value) != {
        "entry_min_amount_rank_pct",
        "min_price",
        "max_price",
        "allow_st",
    }:
        return ["eligibility_parameters fields are invalid"]
    parameters = cast(dict[str, Any], value)
    amount_rank = parameters.get("entry_min_amount_rank_pct")
    min_price = parameters.get("min_price")
    max_price = parameters.get("max_price")
    issues: list[str] = []
    if not _is_finite_number(amount_rank) or not 0.0 <= _safe_float(amount_rank) <= 100.0:
        issues.append("entry_min_amount_rank_pct must be finite in [0, 100]")
    if (
        not _is_finite_number(min_price)
        or not _is_finite_number(max_price)
        or _safe_float(min_price) < 0
        or _safe_float(max_price) < _safe_float(min_price)
    ):
        issues.append("eligibility price range is invalid")
    if not isinstance(parameters.get("allow_st"), bool):
        issues.append("eligibility allow_st must be boolean")
    return issues


def _overlay_identity_issues(result: dict[str, Any]) -> tuple[list[str], str]:
    issues: list[str] = []
    expected = {
        "schema_version": HOLDINGS_OVERLAY_SCHEMA_VERSION,
        "artifact_type": HOLDINGS_OVERLAY_ARTIFACT_TYPE,
        "market": "CN",
        "execution_not_before": "next_trading_session",
        "future_data_included": False,
        "strict_point_in_time": False,
    }
    for field, value in expected.items():
        if result.get(field) != value:
            issues.append(f"{field} is invalid")
    if result.get("feature_policy") != holdings_feature_policy():
        issues.append("feature_policy is not canonical")
    try:
        observation = date_key(result.get("observation_date"))
        cutoff = date_key(result.get("data_cutoff"))
    except ValueError as exc:
        issues.append(str(exc))
        observation = ""
        cutoff = ""
    if observation and observation != cutoff:
        issues.append("data_cutoff must equal observation_date")
    try:
        generated = datetime.fromisoformat(str(result.get("generated_at")))
        if generated.tzinfo is None or generated.utcoffset() is None:
            issues.append("generated_at must include a UTC offset")
    except ValueError:
        issues.append("generated_at must be an ISO-8601 timestamp")
    digest = result.get("candidate_payload_sha256")
    if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
        issues.append("candidate_payload_sha256 must be a SHA-256 digest")
    if result.get("candidate_artifact_type") != "hot_sector_candidate_universe":
        issues.append("candidate_artifact_type is invalid")
    if result.get("candidate_schema_version") not in {"1.0.0", "2.0.0"}:
        issues.append("candidate_schema_version is invalid")
    issues.extend(_eligibility_parameter_issues(result.get("eligibility_parameters")))
    return issues, observation


def _snapshot_identity_issues(value: object, observation: str) -> list[str]:
    if not isinstance(value, dict):
        return ["holdings_snapshot must be an object"]
    snapshot = cast(dict[str, Any], value)
    issues: list[str] = []
    if set(snapshot) != {
        "schema_version",
        "artifact_type",
        "as_of_date",
        "sha256",
        "symbol_count",
    }:
        issues.append("holdings_snapshot fields are invalid")
    if snapshot.get("schema_version") != HOLDINGS_SNAPSHOT_SCHEMA_VERSION:
        issues.append("holdings_snapshot schema_version is invalid")
    if snapshot.get("artifact_type") != HOLDINGS_SNAPSHOT_ARTIFACT_TYPE:
        issues.append("holdings_snapshot artifact_type is invalid")
    digest = snapshot.get("sha256")
    if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
        issues.append("holdings_snapshot sha256 is invalid")
    count = snapshot.get("symbol_count")
    if not isinstance(count, int) or not 0 <= count <= 1_000:
        issues.append("holdings_snapshot symbol_count is invalid")
    try:
        as_of = date_key(snapshot.get("as_of_date"))
        if observation and as_of > observation:
            issues.append("holdings_snapshot as_of_date follows observation_date")
    except ValueError as exc:
        issues.append(str(exc))
    return issues


def _array_issue(row: dict[str, Any], field: str, index: int) -> str | None:
    values = row.get(field)
    if not isinstance(values, list) or not all(
        isinstance(item, str) and bool(item.strip()) for item in values
    ):
        return f"rows[{index}].{field} must be a string array"
    return None


def _theme_issues(row: dict[str, Any], index: int, observation: str) -> list[str]:
    issues = [
        issue
        for field in ("source_topics", "source_concepts")
        if (issue := _array_issue(row, field, index)) is not None
    ]
    if row.get("current_theme_match") is False:
        if (
            row.get("theme_score") != 0.0
            or row.get("theme_relevance") != 0.0
            or row.get("source_topics") != []
            or row.get("source_concepts") != []
            or row.get("last_theme_seen") is not None
            or row.get("theme_age") is not None
        ):
            issues.append(f"rows[{index}] unmatched theme fields must be zero or null")
    elif row.get("current_theme_match") is True:
        if row.get("last_theme_seen") != observation or row.get("theme_age") != 0:
            issues.append(f"rows[{index}] current theme history is inconsistent")
        if not _is_finite_number(row.get("theme_score")):
            issues.append(f"rows[{index}].theme_score must be finite")
        relevance = row.get("theme_relevance")
        if not _is_finite_number(relevance) or not 0.0 <= _safe_float(relevance) <= 1.0:
            issues.append(f"rows[{index}].theme_relevance must be finite in [0, 1]")
    return issues


def _technical_issues(row: dict[str, Any], index: int, observation: str) -> list[str]:
    technical_date = row.get("technical_as_of_date")
    if technical_date is None:
        nullable = ("technical_history_days", *TECHNICAL_FIELDS)
        return (
            [f"rows[{index}] unavailable technical fields must be null"]
            if any(row.get(field) is not None for field in nullable)
            else []
        )
    if technical_date != observation:
        return [f"rows[{index}].technical_as_of_date is stale"]
    history_days = row.get("technical_history_days")
    issues = []
    if not isinstance(history_days, int) or history_days < 3:
        issues.append(f"rows[{index}].technical_history_days is invalid")
    for field in TECHNICAL_FIELDS:
        if not _is_finite_number(row.get(field)):
            issues.append(f"rows[{index}].{field} must be finite")
    return issues


def _eligibility_issues(row: dict[str, Any], index: int) -> list[str]:
    issues: list[str] = []
    for field in ("entry_ineligible_reasons", "hold_ineligible_reasons"):
        issue = _array_issue(row, field, index)
        if issue is not None:
            issues.append(issue)
            continue
        if row[field] != sorted(set(row[field])):
            issues.append(f"rows[{index}].{field} must be sorted and unique")
        eligible_field = field.replace("_ineligible_reasons", "_eligible")
        if row.get(eligible_field) != (len(row[field]) == 0):
            issues.append(f"rows[{index}].{eligible_field} conflicts with reasons")
    if row.get("current_theme_match") is False and "not_current_theme" not in row.get(
        "entry_ineligible_reasons", []
    ):
        issues.append(f"rows[{index}] unmatched theme must be entry-ineligible")
    missing_technical_reason = "current_day_technical_unavailable" not in row.get(
        "hold_ineligible_reasons", []
    )
    if row.get("technical_as_of_date") is None and missing_technical_reason:
        issues.append(f"rows[{index}] missing technical features must be hold-ineligible")
    return issues


def _row_issues(
    row: object,
    index: int,
    observation: str,
    symbols: set[str],
) -> list[str]:
    if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
        return [f"rows[{index}] fields are invalid"]
    item = cast(dict[str, Any], row)
    issues: list[str] = []
    symbol = item.get("ts_code")
    if not isinstance(symbol, str) or _SYMBOL_PATTERN.fullmatch(symbol) is None:
        issues.append(f"rows[{index}].ts_code is invalid")
    elif symbol in symbols:
        issues.append(f"rows[{index}].ts_code is duplicated")
    else:
        symbols.add(symbol)
    if not isinstance(item.get("name"), str):
        issues.append(f"rows[{index}].name must be a string")
    for field in (
        "is_current_holding",
        "entry_eligible",
        "hold_eligible",
        "current_theme_match",
    ):
        if not isinstance(item.get(field), bool):
            issues.append(f"rows[{index}].{field} must be boolean")
    for field in ("close", "amount_rank_pct", "liquidity_score"):
        if item.get(field) is not None and not _is_finite_number(item.get(field)):
            issues.append(f"rows[{index}].{field} must be finite or null")
    issues.extend(_theme_issues(item, index, observation))
    issues.extend(_technical_issues(item, index, observation))
    issues.extend(_eligibility_issues(item, index))
    return issues


def validate_holdings_overlay(payload: object) -> dict[str, Any]:
    """Fail closed when an overlay violates the immutable producer semantics."""

    if not isinstance(payload, dict):
        raise HoldingsOverlayContractError("holdings overlay root must be an object")
    result = cast(dict[str, Any], payload)
    issues = []
    if set(result) != _OVERLAY_FIELDS:
        issues.append("holdings overlay fields are invalid")
    identity_issues, observation = _overlay_identity_issues(result)
    issues.extend(identity_issues)
    issues.extend(_snapshot_identity_issues(result.get("holdings_snapshot"), observation))
    rows = result.get("rows")
    if not isinstance(rows, list):
        issues.append("rows must be an array")
        rows = []
    symbols: set[str] = set()
    for index, row in enumerate(rows):
        issues.extend(_row_issues(row, index, observation, symbols))
    if result.get("row_count") != len(rows):
        issues.append("row_count must equal rows length")
    snapshot = result.get("holdings_snapshot")
    if isinstance(snapshot, dict):
        holdings_count = sum(
            1 for row in rows if isinstance(row, dict) and row.get("is_current_holding") is True
        )
        if snapshot.get("symbol_count") != holdings_count:
            issues.append("holdings_snapshot symbol_count must equal incumbent row count")
    if issues:
        raise HoldingsOverlayContractError("invalid holdings overlay: " + "; ".join(issues))
    return result
