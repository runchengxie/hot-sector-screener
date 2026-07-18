from __future__ import annotations

from typing import Any

from hot_sector_screener.candidate_contract import (
    CANDIDATE_SCHEMA_VERSION_V1,
    CANDIDATE_SCHEMA_VERSION_V2,
    candidate_model_identity,
    source_concepts_policy,
)


def valid_candidate_payload(
    *,
    candidates: list[dict[str, Any]] | None = None,
    data_sources: dict[str, Any] | None = None,
    schema_version: str = CANDIDATE_SCHEMA_VERSION_V2,
) -> dict[str, Any]:
    universe = [dict(candidate) for candidate in candidates or []]
    if schema_version == CANDIDATE_SCHEMA_VERSION_V2:
        for candidate in universe:
            candidate.setdefault("source_event_tags", [])
            candidate.setdefault("source_event_statuses", [])
            candidate.setdefault("source_event_reasons", [])
    deferred = {
        "available": False,
        "reason": "future_data_excluded_from_generation",
        "horizons": {},
    }
    payload = {
        "schema_version": schema_version,
        "artifact_type": "hot_sector_candidate_universe",
        "market": "CN",
        "date": "2026-06-29",
        "date_int": "20260629",
        "observation_date": "20260629",
        "data_cutoff": "20260629",
        "data_cutoff_semantics": "end_of_day",
        "execution_not_before": "next_trading_session",
        "future_data_included": False,
        "generated_at": "2026-06-30T13:48:57+08:00",
        "provenance": {
            "timezone": "Asia/Shanghai",
            "observation_date": "20260629",
            "data_cutoff": "20260629",
            "future_data_included": False,
            "artifact_role": "candidate_universe",
            "strict_point_in_time": False,
            "rotation": {
                "as_of_date": "20260629",
                "signal_date": None,
                "provenance_level": "unavailable",
                "strict_point_in_time": False,
                "publisher_receipt_verified": False,
            },
        },
        "evidence": {
            "strict_point_in_time": False,
            "out_of_sample_claim": False,
            "temporal_context": "post_observation_generation",
            "limitations": [
                "rotation_publisher_receipt_unavailable",
                "candidate_artifact_does_not_establish_out_of_sample_validity",
                "post_observation_reconstruction_not_oos",
            ],
        },
        "topics": [],
        "candidate_universe": universe,
        "universe_size": len(universe),
        "config_snapshot": {"min_candidates": 2},
        "data_sources": data_sources or {},
        "quality_report": dict(deferred),
        "outcome_report": dict(deferred),
    }
    if schema_version == CANDIDATE_SCHEMA_VERSION_V2:
        payload.update(
            {
                "model_identity": candidate_model_identity(),
                "source_concepts_policy": source_concepts_policy(),
            }
        )
    elif schema_version != CANDIDATE_SCHEMA_VERSION_V1:
        raise ValueError(f"unsupported test schema_version: {schema_version}")
    return payload
