from __future__ import annotations

import json
from pathlib import Path

import pytest

from hot_sector_screener.candidate_contract import (
    CandidateContractError,
    validate_candidate_result,
)
from tests.candidate_factory import valid_candidate_payload


def test_current_candidate_contract_passes():
    payload = valid_candidate_payload(
        candidates=[
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "score": 0.8,
                "relevance": 0.8,
                "source_topics": [],
                "source_concepts": [],
            }
        ]
    )

    assert validate_candidate_result(payload) is payload


def test_canonical_v1_example_passes_contract():
    example_path = Path(__file__).parents[1] / "examples" / "candidate_universe.v1.json"
    payload = json.loads(example_path.read_text(encoding="utf-8"))

    assert validate_candidate_result(payload) is payload


def test_legacy_candidate_artifact_fails_closed():
    legacy = {
        "date": "2026-06-29",
        "candidate_universe": [{"ts_code": "000001.SZ"}],
    }

    with pytest.raises(CandidateContractError, match="schema_version"):
        validate_candidate_result(legacy)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("future_data_included", True, "future_data_included"),
        ("generated_at", "2026-06-30T13:48:57", "UTC offset"),
        ("date_int", "20269999", "Invalid calendar date"),
    ],
)
def test_candidate_contract_rejects_unsafe_provenance(field, value, message):
    payload = valid_candidate_payload()
    payload[field] = value

    with pytest.raises(CandidateContractError, match=message):
        validate_candidate_result(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("score", None, "score must be finite"),
        ("score", float("nan"), "score must be finite"),
        ("relevance", float("inf"), "relevance must be finite"),
        ("name", "", "name must be 1-64"),
        ("source_topics", "半导体", "source_topics must be a string array"),
    ],
)
def test_candidate_contract_rejects_invalid_candidate_rows(field, value, message):
    payload = valid_candidate_payload(
        candidates=[
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "score": 0.8,
                "relevance": 0.8,
                "source_topics": ["半导体"],
                "source_concepts": ["半导体设备"],
            }
        ]
    )
    payload["candidate_universe"][0][field] = value

    with pytest.raises(CandidateContractError, match=message):
        validate_candidate_result(payload)


def test_candidate_contract_rejects_duplicate_symbols():
    row = {
        "ts_code": "000001.SZ",
        "name": "平安银行",
        "score": 0.8,
        "relevance": 0.8,
        "source_topics": ["半导体"],
        "source_concepts": ["半导体设备"],
    }
    payload = valid_candidate_payload(candidates=[dict(row), dict(row)])

    with pytest.raises(CandidateContractError, match="duplicated"):
        validate_candidate_result(payload)
