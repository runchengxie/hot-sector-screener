from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hot_sector_screener.candidate_contract import (
    CANDIDATE_SCHEMA_VERSION_V1,
    CANDIDATE_SCHEMA_VERSION_V2,
    CandidateContractError,
    candidate_contract_info,
    source_concepts_policy,
    validate_candidate_result,
    validate_candidate_result_v1,
    validate_candidate_result_v2,
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

    assert payload["schema_version"] == CANDIDATE_SCHEMA_VERSION_V2
    assert validate_candidate_result_v2(payload) is payload


def test_canonical_v1_example_passes_contract():
    example_path = Path(__file__).parents[1] / "examples" / "candidate_universe.v1.json"
    payload = json.loads(example_path.read_text(encoding="utf-8"))

    assert validate_candidate_result_v1(payload) is payload


def test_canonical_v2_example_passes_contract():
    example_path = Path(__file__).parents[1] / "examples" / "candidate_universe.v2.json"
    payload = json.loads(example_path.read_text(encoding="utf-8"))

    assert validate_candidate_result_v2(payload) is payload


def test_v1_remains_readable_without_v2_event_or_policy_fields():
    payload = valid_candidate_payload(schema_version=CANDIDATE_SCHEMA_VERSION_V1)

    assert "source_concepts_policy" not in payload
    assert validate_candidate_result(payload) is payload


def test_v2_policy_hash_uses_canonical_json_without_the_hash_field():
    policy = source_concepts_policy()
    hash_value = policy.pop("canonical_sha256")
    encoded = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert hash_value == hashlib.sha256(encoded).hexdigest()
    assert candidate_contract_info()["source_concepts_policy"]["canonical_sha256"] == hash_value


def test_v2_rejects_policy_or_model_identity_drift():
    payload = valid_candidate_payload()
    payload["source_concepts_policy"]["allowed"].append("tag")
    payload["model_identity"]["feature_set_id"] = "legacy-feature-set"

    with pytest.raises(
        CandidateContractError,
        match=r"model_identity.*source_concepts_policy",
    ):
        validate_candidate_result_v2(payload)


def test_v2_requires_separate_event_metadata_arrays():
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
    del payload["candidate_universe"][0]["source_event_reasons"]

    with pytest.raises(CandidateContractError, match="source_event_reasons"):
        validate_candidate_result_v2(payload)


def test_pinned_v2_validator_rejects_valid_v1():
    payload = valid_candidate_payload(schema_version=CANDIDATE_SCHEMA_VERSION_V1)

    with pytest.raises(CandidateContractError, match=r"schema_version must be 2\.0\.0"):
        validate_candidate_result_v2(payload)


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
