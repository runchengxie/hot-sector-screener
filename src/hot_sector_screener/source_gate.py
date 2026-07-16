from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

SOURCE_GATE_SCHEMA = "hotsector_source_gate.v1"
SOURCE_MODES = frozenset({"normal", "dc_fallback", "event_fallback", "blocked"})
DELIVERABLE_SOURCE_MODES = frozenset({"normal", "dc_fallback", "event_fallback"})
EVENT_CONFIRMATION_SOURCES = (
    "limit_list_ths",
    "limit_step",
    "limit_cpt_list",
    "ths_hot",
)
MIN_EVENT_CONFIRMATIONS = 2
KPL_CONCEPT_CONS_ROW_LIMIT = 3000
_STOCK_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")


def _date_key(value: object) -> str | None:
    text = str(value or "").strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else None


def frame_source_status(frame: pd.DataFrame, observation_date: str) -> dict[str, Any]:
    """Describe whether a frame is non-empty and belongs only to the requested date."""

    expected = _date_key(observation_date)
    observed: set[str] = set()
    invalid_trade_date_rows = 0
    if "trade_date" in frame.columns:
        normalized = [_date_key(raw) for raw in frame["trade_date"].tolist()]
        observed = {date for date in normalized if date is not None}
        invalid_trade_date_rows = sum(date is None for date in normalized)
    elif frame.attrs.get("observed_trade_dates"):
        observed = {
            date
            for raw in frame.attrs["observed_trade_dates"]
            if (date := _date_key(raw)) is not None
        }
        invalid_trade_date_rows = int(frame.attrs.get("invalid_trade_date_rows") or 0)

    available = not frame.empty
    requested = _date_key(frame.attrs.get("requested_trade_date"))
    exact_date = bool(
        available
        and expected
        and observed == {expected}
        and invalid_trade_date_rows == 0
        and (requested is None or requested == expected)
    )
    status: dict[str, Any] = {
        "available": available,
        "exact_date": exact_date,
        "row_count": len(frame),
        "observed_trade_dates": sorted(observed),
        "invalid_trade_date_rows": invalid_trade_date_rows,
    }
    completeness = frame.attrs.get("completeness")
    if isinstance(completeness, Mapping):
        status["completeness"] = dict(completeness)
        if completeness.get("complete") is False:
            # Any explicit negative/incomplete receipt overrides a physically
            # retained partition for production and additional fixed gates.
            status["available"] = False
            status["unusable_partition_retained"] = not frame.empty
            retained_negative_receipt = (
                completeness.get("reason") == "empty_refresh_receipt"
                or completeness.get("source") == "staged_negative_receipt"
            )
            if retained_negative_receipt:
                exact_date = False
                status["exact_date"] = False
                status["last_known_good_retained"] = not frame.empty
        status["complete"] = bool(exact_date and frame.attrs.get("completeness_verified") is True)
    return status


def _kpl_mapping_key_status(frame: pd.DataFrame) -> dict[str, Any]:
    required = ("name", "con_code", "con_name")
    missing = [column for column in required if column not in frame.columns]
    if frame.empty or missing:
        return {
            "valid": False,
            "required_columns": list(required),
            "missing_columns": missing,
            "usable_row_count": 0,
            "coverage_ratio": 0.0,
        }

    def clean(column: str) -> pd.Series:
        return frame[column].map(lambda value: str(value).strip() if pd.notna(value) else "")

    concepts = clean("name")
    stock_codes = clean("con_code")
    stock_names = clean("con_name")
    usable = (
        concepts.ne("")
        & ~concepts.str.lower().isin({"nan", "none"})
        & stock_codes.str.fullmatch(_STOCK_CODE_PATTERN)
        & stock_names.ne("")
        & ~stock_names.str.lower().isin({"nan", "none"})
    )
    usable_count = int(usable.sum())
    return {
        "valid": usable_count == len(frame),
        "required_columns": list(required),
        "missing_columns": [],
        "usable_row_count": usable_count,
        "coverage_ratio": usable_count / len(frame),
    }


def build_source_gate(frames: Mapping[str, pd.DataFrame], observation_date: str) -> dict[str, Any]:
    """Classify one candidate run into the auditable four-state source policy."""

    expected = _date_key(observation_date)
    if expected is None:
        raise ValueError("observation_date must be YYYYMMDD or YYYY-MM-DD")
    statuses = {source: frame_source_status(frame, expected) for source, frame in frames.items()}
    kpl_status = statuses.get("kpl_concept_cons", {})
    kpl_frame = frames.get("kpl_concept_cons", pd.DataFrame())
    kpl_mapping_keys = _kpl_mapping_key_status(kpl_frame)
    kpl_status["mapping_keys"] = kpl_mapping_keys
    if kpl_status.get("exact_date") is True:
        if kpl_mapping_keys["valid"] is not True:
            kpl_status["complete"] = False
            kpl_status["completeness_basis"] = "mapping_keys_invalid"
        elif kpl_status.get("complete") is True:
            kpl_status["completeness_basis"] = "manifest"
        elif "complete" in kpl_status:
            # An explicit per-date receipt always wins over row-count inference.
            kpl_status["completeness_basis"] = "manifest_incomplete"
        elif int(kpl_status.get("row_count") or 0) < KPL_CONCEPT_CONS_ROW_LIMIT:
            # kpl_concept_cons is a one-page API. A non-empty response below
            # its documented row limit proves that the response was not capped.
            kpl_status["complete"] = True
            kpl_status["completeness_basis"] = "below_api_row_limit"
        else:
            kpl_status["complete"] = False
            kpl_status["completeness_basis"] = "api_row_limit_reached"
    event_sources = [
        source
        for source in EVENT_CONFIRMATION_SOURCES
        if statuses.get(source, {}).get("exact_date") is True
    ]
    events_sufficient = len(event_sources) >= MIN_EVENT_CONFIRMATIONS
    kpl_complete = bool(kpl_status.get("exact_date") is True and kpl_status.get("complete") is True)
    dc_mapping_complete = bool(
        statuses.get("dc_concept", {}).get("exact_date") is True
        and statuses.get("dc_concept_cons", {}).get("exact_date") is True
        and statuses.get("dc_concept_cons", {}).get("complete") is True
    )

    if not events_sufficient:
        mode = "blocked"
        fallback_reason = "insufficient_same_day_event_sources"
    elif kpl_complete:
        mode = "normal"
        fallback_reason = None
    elif dc_mapping_complete:
        mode = "dc_fallback"
        fallback_reason = "kpl_concept_cons_exact_date_unavailable"
    else:
        mode = "event_fallback"
        fallback_reason = "complete_member_mapping_unavailable"

    return {
        "schema_version": SOURCE_GATE_SCHEMA,
        "observation_date": expected,
        "source_mode": mode,
        "fallback_reason": fallback_reason,
        "mapping": {
            "kpl_complete": kpl_complete,
            "dc_complete": dc_mapping_complete,
        },
        "event_confirmation": {
            "minimum_required": MIN_EVENT_CONFIRMATIONS,
            "available_count": len(event_sources),
            "sources": event_sources,
        },
        "sources": statuses,
    }


def _gate_identity_issues(payload: Mapping[str, Any], gate: Mapping[str, Any]) -> list[str]:
    mode = payload.get("source_mode")
    fallback_reason = payload.get("fallback_reason")
    issues: list[str] = []
    if gate.get("schema_version") != SOURCE_GATE_SCHEMA:
        issues.append(f"source_gate.schema_version must be {SOURCE_GATE_SCHEMA}")
    if mode not in SOURCE_MODES:
        issues.append("source_mode is invalid")
    if gate.get("source_mode") != mode:
        issues.append("source_gate.source_mode must match source_mode")
    expected_date = _date_key(payload.get("observation_date"))
    if _date_key(gate.get("observation_date")) != expected_date:
        issues.append("source_gate.observation_date must match observation_date")
    if gate.get("fallback_reason") != fallback_reason:
        issues.append("source_gate.fallback_reason must match fallback_reason")
    if mode == "normal" and fallback_reason is not None:
        issues.append("normal source_mode requires a null fallback_reason")
    if mode != "normal" and not isinstance(fallback_reason, str):
        issues.append("non-normal source_mode requires fallback_reason")
    return issues


def _gate_components(
    gate: Mapping[str, Any],
) -> tuple[list[str], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    issues: list[str] = []
    values: list[Mapping[str, Any]] = []
    for field in ("mapping", "event_confirmation", "sources"):
        value = gate.get(field)
        if not isinstance(value, Mapping):
            issues.append(f"source_gate.{field} must be an object")
            value = {}
        values.append(value)
    return issues, values[0], values[1], values[2]


def _event_metadata_issues(
    event_confirmation: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    raw_events = event_confirmation.get("sources")
    available_events = (
        [source for source in raw_events if isinstance(source, str)]
        if isinstance(raw_events, list)
        else []
    )
    raw_count = len(raw_events) if isinstance(raw_events, list) else -1
    if (
        any(source not in EVENT_CONFIRMATION_SOURCES for source in available_events)
        or len(available_events) != raw_count
        or len(set(available_events)) != len(available_events)
    ):
        available_events = []
    if available_events != raw_events:
        issues.append("source_gate.event_confirmation.sources is invalid")
    if event_confirmation.get("minimum_required") != MIN_EVENT_CONFIRMATIONS:
        issues.append(
            f"source_gate.event_confirmation.minimum_required must be {MIN_EVENT_CONFIRMATIONS}"
        )
    if event_confirmation.get("available_count") != len(available_events):
        issues.append("source_gate.event_confirmation.available_count is inconsistent")
    return issues, available_events


def _capability_metadata_issues(
    mode: object,
    mapping: Mapping[str, Any],
    sources: Mapping[str, Any],
    available_events: list[str],
) -> list[str]:
    issues: list[str] = []
    kpl_complete = mapping.get("kpl_complete") is True
    dc_complete = mapping.get("dc_complete") is True
    if len(available_events) < MIN_EVENT_CONFIRMATIONS:
        expected_mode = "blocked"
    elif kpl_complete:
        expected_mode = "normal"
    elif dc_complete:
        expected_mode = "dc_fallback"
    else:
        expected_mode = "event_fallback"
    if mode != expected_mode:
        issues.append(f"source_mode must be {expected_mode} for the recorded capabilities")

    required_complete_sources = {
        "dc_concept_cons": dc_complete,
        "kpl_concept_cons": kpl_complete,
    }
    for source, required in required_complete_sources.items():
        status = sources.get(source)
        if required and (
            not isinstance(status, Mapping)
            or status.get("exact_date") is not True
            or status.get("complete") is not True
        ):
            issues.append(
                f"{source.removesuffix('_concept_cons')}_complete requires "
                f"exact-date complete {source} metadata"
            )
    raw_kpl_status = sources.get("kpl_concept_cons")
    kpl_status = raw_kpl_status if isinstance(raw_kpl_status, Mapping) else {}
    kpl_keys = kpl_status.get("mapping_keys")
    if kpl_complete and (
        not isinstance(kpl_keys, Mapping)
        or kpl_keys.get("valid") is not True
        or kpl_keys.get("coverage_ratio") != 1.0
        or kpl_keys.get("usable_row_count") != kpl_status.get("row_count")
        or kpl_keys.get("missing_columns") != []
    ):
        issues.append("kpl_complete requires fully populated KPL mapping keys")
    dc_concept_status = sources.get("dc_concept")
    if dc_complete and (
        not isinstance(dc_concept_status, Mapping)
        or dc_concept_status.get("exact_date") is not True
    ):
        issues.append("dc_complete requires exact-date dc_concept metadata")
    for source in available_events:
        source_status = sources.get(source)
        if not isinstance(source_status, Mapping) or source_status.get("exact_date") is not True:
            issues.append(f"event source is not exact-date: {source}")
    return issues


def source_gate_issues(payload: Mapping[str, Any]) -> list[str]:
    """Validate new source-gate metadata; legacy candidates remain readable."""

    gate = payload.get("source_gate")
    mode = payload.get("source_mode")
    if gate is None and mode is None and "fallback_reason" not in payload:
        return []
    if not isinstance(gate, Mapping):
        return ["source_gate must be an object when source_mode metadata is present"]

    issues = _gate_identity_issues(payload, gate)
    shape_issues, mapping, event_confirmation, sources = _gate_components(gate)
    event_issues, available_events = _event_metadata_issues(event_confirmation)
    issues.extend(shape_issues)
    issues.extend(event_issues)
    issues.extend(_capability_metadata_issues(mode, mapping, sources, available_events))
    return issues


def production_source_issues(payload: Mapping[str, Any]) -> list[str]:
    """Return fail-closed production issues, with normal-only legacy compatibility."""

    gate = payload.get("source_gate")
    if isinstance(gate, Mapping):
        issues = source_gate_issues(payload)
        if payload.get("source_mode") not in DELIVERABLE_SOURCE_MODES:
            reason = payload.get("fallback_reason") or "source capabilities unavailable"
            issues.append(f"source capability gate blocked: {reason}")
        return issues

    # Old candidate artifacts never carried completeness receipts. They can be
    # reused only on the old normal path; fallback must never infer completeness
    # from a merely non-empty DC partition.
    data_sources = payload.get("data_sources")
    if not isinstance(data_sources, Mapping):
        return ["source capability metadata is missing"]
    available_events = sum(
        data_sources.get(f"{source}_available") is True for source in EVENT_CONFIRMATION_SOURCES
    )
    if (
        data_sources.get("kpl_concept_cons_available") is True
        and available_events >= MIN_EVENT_CONFIRMATIONS
    ):
        return []
    return ["source capability metadata is missing; legacy fallback is not allowed"]


__all__ = [
    "DELIVERABLE_SOURCE_MODES",
    "EVENT_CONFIRMATION_SOURCES",
    "KPL_CONCEPT_CONS_ROW_LIMIT",
    "MIN_EVENT_CONFIRMATIONS",
    "SOURCE_GATE_SCHEMA",
    "SOURCE_MODES",
    "build_source_gate",
    "frame_source_status",
    "production_source_issues",
    "source_gate_issues",
]
