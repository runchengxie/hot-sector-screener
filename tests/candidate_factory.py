from __future__ import annotations

from typing import Any


def valid_candidate_payload(
    *,
    candidates: list[dict[str, Any]] | None = None,
    data_sources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    universe = candidates or []
    deferred = {
        "available": False,
        "reason": "future_data_excluded_from_generation",
        "horizons": {},
    }
    return {
        "schema_version": "1.0.0",
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
