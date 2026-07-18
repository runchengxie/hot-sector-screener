from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any, cast

from .observation_time import (
    MARKET_TIMEZONE,
    MARKET_TIMEZONE_NAME,
    OBSERVATION_COMPLETE_TIME,
    date_key,
)
from .source_gate import source_gate_issues

CANDIDATE_SCHEMA_VERSION_V1 = "1.0.0"
CANDIDATE_SCHEMA_VERSION_V2 = "2.0.0"
CANDIDATE_SCHEMA_VERSION = CANDIDATE_SCHEMA_VERSION_V2
CANDIDATE_SUPPORTED_SCHEMA_VERSIONS = frozenset(
    {CANDIDATE_SCHEMA_VERSION_V1, CANDIDATE_SCHEMA_VERSION_V2}
)
CANDIDATE_ARTIFACT_TYPE = "hot_sector_candidate_universe"
CANDIDATE_MARKET = "CN"
CANDIDATE_MODEL_ID = "hotsector-theme-v3"
CANDIDATE_MODEL_VERSION = "3.0.0"
CANDIDATE_FEATURE_SET_ID = "topic-concept-hotspot-overlay-theme-only-v1"
SOURCE_CONCEPTS_POLICY_ID = "hotsector.source_concepts.theme_only"
SOURCE_CONCEPTS_POLICY_VERSION = "1.0.0"
SOURCE_CONCEPTS_NORMALIZER_ID = "hotsector.concept_token.v1"
SOURCE_CONCEPTS_ALLOWED_FIELDS = ("theme", "concept", "related_concepts")
SOURCE_CONCEPTS_EXCLUDED_FIELDS = (
    "tag",
    "lu_desc",
    "status",
    "rank_reason",
    "limit_type",
)
_SYMBOL_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_TOPIC_FIELDS = frozenset({"topic", "weight", "reasoning", "related_concepts", "source_signals"})


class CandidateContractError(ValueError):
    pass


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_concepts_policy() -> dict[str, Any]:
    """Return the immutable v2 concept-provenance policy and its canonical hash."""
    policy: dict[str, Any] = {
        "policy_id": SOURCE_CONCEPTS_POLICY_ID,
        "version": SOURCE_CONCEPTS_POLICY_VERSION,
        "allowed": list(SOURCE_CONCEPTS_ALLOWED_FIELDS),
        "excluded": list(SOURCE_CONCEPTS_EXCLUDED_FIELDS),
        "normalizer_id": SOURCE_CONCEPTS_NORMALIZER_ID,
    }
    policy["canonical_sha256"] = _canonical_sha256(policy)
    return policy


def candidate_model_identity() -> dict[str, str]:
    """Return the deterministic v2 feature/model identity."""
    return {
        "model_id": CANDIDATE_MODEL_ID,
        "model_version": CANDIDATE_MODEL_VERSION,
        "feature_set_id": CANDIDATE_FEATURE_SET_ID,
    }


def candidate_contract_info() -> dict[str, Any]:
    """Expose owner-generated contract identity for downstream consumers."""
    return {
        "artifact_type": CANDIDATE_ARTIFACT_TYPE,
        "current_schema_version": CANDIDATE_SCHEMA_VERSION,
        "supported_schema_versions": sorted(CANDIDATE_SUPPORTED_SCHEMA_VERSIONS),
        "model_identity": candidate_model_identity(),
        "source_concepts_policy": source_concepts_policy(),
    }


def _generated_at_issue(value: object, observation_date: str) -> str | None:
    try:
        generated = datetime.fromisoformat(str(value))
    except ValueError:
        return "generated_at must be an ISO-8601 timestamp"
    if generated.tzinfo is None or generated.utcoffset() is None:
        return "generated_at must include a UTC offset"
    generated_shanghai = generated.astimezone(MARKET_TIMEZONE)
    observation = datetime.strptime(observation_date, "%Y%m%d").date()
    if generated_shanghai.date() < observation:
        return "generated_at precedes observation_date"
    if (
        generated_shanghai.date() == observation
        and generated_shanghai.timetz().replace(tzinfo=None) < OBSERVATION_COMPLETE_TIME
    ):
        return "same-day generated_at precedes completed EOD cutoff"
    return None


def _expected_temporal_context(payload: dict[str, Any]) -> str | None:
    try:
        observation = datetime.strptime(date_key(payload.get("observation_date")), "%Y%m%d").date()
        generated = datetime.fromisoformat(str(payload.get("generated_at")))
    except ValueError:
        return None
    if generated.tzinfo is None or generated.utcoffset() is None:
        return None
    generated_date = generated.astimezone(MARKET_TIMEZONE).date()
    return (
        "same_day_eod_generation"
        if generated_date == observation
        else "post_observation_generation"
    )


def _identity_and_date_issues(payload: dict[str, Any]) -> tuple[list[str], str]:
    issues: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version not in CANDIDATE_SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(CANDIDATE_SUPPORTED_SCHEMA_VERSIONS))
        issues.append(f"schema_version must be one of: {supported}")
    if payload.get("artifact_type") != CANDIDATE_ARTIFACT_TYPE:
        issues.append(f"artifact_type must be {CANDIDATE_ARTIFACT_TYPE}")
    if payload.get("market") != CANDIDATE_MARKET:
        issues.append(f"market must be {CANDIDATE_MARKET}")
    if schema_version == CANDIDATE_SCHEMA_VERSION_V2:
        if payload.get("model_identity") != candidate_model_identity():
            issues.append("model_identity must match the canonical v2 identity")
        if payload.get("source_concepts_policy") != source_concepts_policy():
            issues.append("source_concepts_policy must match the canonical v2 policy")

    try:
        observation_date = date_key(payload.get("observation_date"))
        date_int = date_key(payload.get("date_int"))
        display_date = date_key(payload.get("date"))
        data_cutoff = date_key(payload.get("data_cutoff"))
    except ValueError as exc:
        issues.append(str(exc))
        observation_date = ""
        date_int = ""
        display_date = ""
        data_cutoff = ""
    if observation_date and len({observation_date, date_int, display_date, data_cutoff}) != 1:
        issues.append("date, date_int, observation_date, and data_cutoff must match")

    if observation_date:
        generated_issue = _generated_at_issue(payload.get("generated_at"), observation_date)
        if generated_issue:
            issues.append(generated_issue)
    if payload.get("data_cutoff_semantics") != "end_of_day":
        issues.append("data_cutoff_semantics must be end_of_day")
    if payload.get("execution_not_before") != "next_trading_session":
        issues.append("execution_not_before must be next_trading_session")
    if payload.get("future_data_included") is not False:
        issues.append("future_data_included must be false")
    return issues, observation_date


def _string_array_issue(candidate: dict[str, Any], index: int, field: str) -> str | None:
    values = candidate.get(field)
    if not isinstance(values, list) or not all(
        isinstance(value, str) and bool(value.strip()) for value in values
    ):
        return f"candidate_universe[{index}].{field} must be a string array"
    return None


def _candidate_row_issues(
    candidate: dict[str, Any],
    index: int,
    symbols: set[str],
    *,
    schema_version: object,
) -> list[str]:
    issues: list[str] = []
    symbol = str(candidate.get("ts_code", ""))
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        issues.append(f"candidate_universe[{index}].ts_code is invalid: {symbol!r}")
    elif symbol in symbols:
        issues.append(f"candidate_universe[{index}].ts_code is duplicated: {symbol}")
    symbols.add(symbol)
    name = candidate.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
        issues.append(f"candidate_universe[{index}].name must be 1-64 characters")
    if not _is_finite_number(candidate.get("score")):
        issues.append(f"candidate_universe[{index}].score must be finite")
    relevance = candidate.get("relevance")
    if (
        isinstance(relevance, bool)
        or not isinstance(relevance, (int, float))
        or not math.isfinite(float(relevance))
        or not 0.0 <= float(relevance) <= 1.0
    ):
        issues.append(f"candidate_universe[{index}].relevance must be finite in [0, 1]")
    fields = ["source_topics", "source_concepts"]
    if schema_version == CANDIDATE_SCHEMA_VERSION_V2:
        fields.extend(["source_event_tags", "source_event_statuses", "source_event_reasons"])
    issues.extend(
        issue
        for field in fields
        if (issue := _string_array_issue(candidate, index, field)) is not None
    )
    return issues


def _candidate_payload_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    candidates = payload.get("candidate_universe")
    if not isinstance(candidates, list):
        issues.append("candidate_universe must be an array")
        candidates = []
    symbols: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            issues.append(f"candidate_universe[{index}] must be an object")
            continue
        issues.extend(
            _candidate_row_issues(
                candidate,
                index,
                symbols,
                schema_version=payload.get("schema_version"),
            )
        )
    if payload.get("universe_size") != len(candidates):
        issues.append("universe_size must equal candidate_universe length")
    issues.extend(_topic_issues(payload.get("topics")))
    if not isinstance(payload.get("data_sources"), dict):
        issues.append("data_sources must be an object")
    if not isinstance(payload.get("config_snapshot"), dict):
        issues.append("config_snapshot must be an object")
    issues.extend(source_gate_issues(payload))
    return issues


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _topic_issues(value: object) -> list[str]:
    if not isinstance(value, list):
        return ["topics must be an array"]
    issues: list[str] = []
    for index, topic in enumerate(value):
        if not isinstance(topic, dict) or set(topic) != _TOPIC_FIELDS:
            issues.append(f"topics[{index}] must use the exact topic schema")
            continue
        topic_payload = cast(dict[str, Any], topic)
        topic_name = topic_payload.get("topic")
        if not isinstance(topic_name, str) or not topic_name.strip():
            issues.append(f"topics[{index}].topic must be non-empty")
        weight = topic_payload.get("weight")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(float(weight))
            or not 0.0 <= float(weight) <= 1.0
        ):
            issues.append(f"topics[{index}].weight must be finite in [0, 1]")
        if not isinstance(topic_payload.get("reasoning"), str):
            issues.append(f"topics[{index}].reasoning must be a string")
        for field in ("related_concepts", "source_signals"):
            items = topic_payload.get(field)
            if not isinstance(items, list) or not all(
                isinstance(item, str) and bool(item.strip()) for item in items
            ):
                issues.append(f"topics[{index}].{field} must be a non-empty string array")
    return issues


def _rotation_issues(provenance: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    rotation = provenance.get("rotation")
    if not isinstance(rotation, dict):
        return ["provenance.rotation must be an object"]
    if rotation.get("provenance_level") not in {"signal_date_only", "unavailable"}:
        issues.append("provenance.rotation.provenance_level is invalid")
    if rotation.get("strict_point_in_time") is not False:
        issues.append("provenance.rotation.strict_point_in_time must be false")
    if rotation.get("publisher_receipt_verified") is not False:
        issues.append("provenance.rotation.publisher_receipt_verified must be false")
    try:
        as_of_date = date_key(rotation.get("as_of_date"))
    except ValueError:
        issues.append("provenance.rotation.as_of_date must be a valid calendar date")
        as_of_date = ""
    signal_date_value = rotation.get("signal_date")
    signal_date = ""
    if signal_date_value is not None:
        try:
            signal_date = date_key(signal_date_value)
        except ValueError:
            issues.append("provenance.rotation.signal_date must be a valid calendar date or null")
    if as_of_date and signal_date and signal_date > as_of_date:
        issues.append("provenance.rotation.signal_date must not exceed as_of_date")
    if rotation.get("provenance_level") == "signal_date_only" and not signal_date:
        issues.append("signal_date_only rotation provenance requires signal_date")
    if rotation.get("provenance_level") == "unavailable" and signal_date_value is not None:
        issues.append("unavailable rotation provenance requires a null signal_date")
    return issues


def _provenance_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        return ["provenance must be an object"]
    if provenance.get("timezone") != MARKET_TIMEZONE_NAME:
        issues.append(f"provenance.timezone must be {MARKET_TIMEZONE_NAME}")
    if provenance.get("future_data_included") is not False:
        issues.append("provenance.future_data_included must be false")
    if provenance.get("artifact_role") != "candidate_universe":
        issues.append("provenance.artifact_role must be candidate_universe")
    if provenance.get("strict_point_in_time") is not False:
        issues.append("provenance.strict_point_in_time must be false without publisher receipts")
    for field in ("observation_date", "data_cutoff"):
        try:
            value = date_key(provenance.get(field))
        except ValueError:
            issues.append(f"provenance.{field} must be a valid calendar date")
            continue
        try:
            expected = date_key(payload.get(field))
        except ValueError:
            continue
        if value != expected:
            issues.append(f"provenance.{field} must match {field}")
    rotation = provenance.get("rotation")
    if isinstance(rotation, dict):
        try:
            rotation_as_of = date_key(rotation.get("as_of_date"))
            observation_date = date_key(payload.get("observation_date"))
        except ValueError:
            pass
        else:
            if rotation_as_of != observation_date:
                issues.append("provenance.rotation.as_of_date must match observation_date")
    issues.extend(_rotation_issues(provenance))
    return issues


def _evidence_issues(payload: dict[str, Any]) -> list[str]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        return ["evidence must be an object"]
    issues: list[str] = []
    if evidence.get("strict_point_in_time") is not False:
        issues.append("evidence.strict_point_in_time must be false")
    if evidence.get("out_of_sample_claim") is not False:
        issues.append("evidence.out_of_sample_claim must be false")
    temporal_context = evidence.get("temporal_context")
    if temporal_context not in {"same_day_eod_generation", "post_observation_generation"}:
        issues.append("evidence.temporal_context is invalid")
    expected_context = _expected_temporal_context(payload)
    if expected_context and temporal_context != expected_context:
        issues.append(f"evidence.temporal_context must be {expected_context}")
    limitations = evidence.get("limitations")
    if (
        not isinstance(limitations, list)
        or "rotation_publisher_receipt_unavailable" not in limitations
    ):
        issues.append("evidence must disclose missing rotation publisher receipt")
    if (
        isinstance(limitations, list)
        and "candidate_artifact_does_not_establish_out_of_sample_validity" not in limitations
    ):
        issues.append("evidence must disclaim out-of-sample validity")
    if (
        temporal_context == "post_observation_generation"
        and isinstance(limitations, list)
        and "post_observation_reconstruction_not_oos" not in limitations
    ):
        issues.append("post-observation generation must disclose reconstruction limits")
    return issues


def _deferred_evaluation_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    expected_stub = {
        "available": False,
        "reason": "future_data_excluded_from_generation",
        "horizons": {},
    }
    if payload.get("quality_report") != expected_stub:
        issues.append("quality_report must be the generation-time deferred stub")
    if payload.get("outcome_report") != expected_stub:
        issues.append("outcome_report must be the generation-time deferred stub")
    return issues


def validate_candidate_result(payload: object) -> dict[str, Any]:
    """Validate a supported v1/v2 artifact and fail closed on unknown input."""
    if not isinstance(payload, dict):
        raise CandidateContractError("candidate artifact root must be an object")
    typed_payload = cast(dict[str, Any], payload)

    issues, _ = _identity_and_date_issues(typed_payload)
    issues.extend(_candidate_payload_issues(typed_payload))
    issues.extend(_provenance_issues(typed_payload))
    issues.extend(_evidence_issues(typed_payload))
    issues.extend(_deferred_evaluation_issues(typed_payload))

    if issues:
        raise CandidateContractError("invalid candidate artifact: " + "; ".join(issues))
    return typed_payload


def validate_candidate_result_v1(payload: object) -> dict[str, Any]:
    """Validate and pin a legacy v1 artifact without upgrading or rewriting it."""
    result = validate_candidate_result(payload)
    if result.get("schema_version") != CANDIDATE_SCHEMA_VERSION_V1:
        raise CandidateContractError(
            f"invalid candidate artifact: schema_version must be {CANDIDATE_SCHEMA_VERSION_V1}"
        )
    return result


def validate_candidate_result_v2(payload: object) -> dict[str, Any]:
    """Validate and pin a candidate v2 artifact for new consumers."""
    result = validate_candidate_result(payload)
    if result.get("schema_version") != CANDIDATE_SCHEMA_VERSION_V2:
        raise CandidateContractError(
            f"invalid candidate artifact: schema_version must be {CANDIDATE_SCHEMA_VERSION_V2}"
        )
    return result
