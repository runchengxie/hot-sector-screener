"""Assemble the candidate-universe result payload and validate it.

Kept in a dedicated small module so `universe_builder.py` stays below the
maintainability ratchet's 800-line file budget.
"""

from __future__ import annotations

from typing import Any

from .candidate_contract import (
    CANDIDATE_ARTIFACT_TYPE,
    CANDIDATE_MARKET,
    CANDIDATE_SCHEMA_VERSION,
    candidate_model_identity,
    source_concepts_policy,
    validate_candidate_result,
)
from .universe_builder import (
    _contract_evidence,
    _data_source_status,
    _deferred_evaluation_report,
)


def build_result_payload(
    date_int: str,
    date_str: str,
    frames: dict[str, Any],
    topics: object,
    filtered: list[dict[str, Any]],
    topic_classification_lineage: dict[str, Any],
    config_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the candidate-result dict and validate it against the contract."""
    ths = frames["ths"]
    dc = frames["dc"]
    dc_cons = frames["dc_cons"]
    kpl_cons = frames["kpl_cons"]
    kpl_list = frames["kpl_list"]
    limit_step = frames["limit_step"]
    limit_cpt = frames["limit_cpt"]
    limit_list_ths = frames["limit_list_ths"]
    hf = frames["hf"]
    daily = frames["daily"]
    daily_history = frames["daily_history"]
    ind_signal = frames["ind_signal"]
    source_gate = frames["source_gate"]

    result = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "artifact_type": CANDIDATE_ARTIFACT_TYPE,
        "model_identity": candidate_model_identity(),
        "source_concepts_policy": source_concepts_policy(),
        "market": CANDIDATE_MARKET,
        "date": date_str,
        "date_int": date_int,
        "observation_date": date_int,
        "data_cutoff": date_int,
        "data_cutoff_semantics": "end_of_day",
        "execution_not_before": "next_trading_session",
        "future_data_included": False,
        **_contract_evidence(date_int, ind_signal),
        "source_mode": source_gate["source_mode"],
        "fallback_reason": source_gate["fallback_reason"],
        "source_gate": source_gate,
        "topics": topics,
        "candidate_universe": filtered,
        "universe_size": len(filtered),
        "config_snapshot": config_snapshot,
        "data_sources": _data_source_status(
            {
                "ths_hot": ths,
                "dc_concept": dc,
                "dc_concept_cons": dc_cons,
                "kpl_concept_cons": kpl_cons,
                "kpl_list": kpl_list,
                "limit_step": limit_step,
                "limit_cpt_list": limit_cpt,
                "limit_list_ths": limit_list_ths,
                "hotspot_features": hf,
                "daily": daily,
                "daily_history": daily_history,
                "industry_signal": ind_signal,
            },
            source_gate,
        ),
        "quality_report": _deferred_evaluation_report(),
        "outcome_report": _deferred_evaluation_report(),
    }
    return validate_candidate_result(result)
