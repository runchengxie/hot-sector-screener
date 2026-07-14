from __future__ import annotations

import json

import pandas as pd
import pytest

from hot_sector_screener.candidate_contract import CandidateContractError
from hot_sector_screener.signal_export import (
    SIGNAL_COLUMNS,
    build_signal_frame,
    load_candidate_result,
    write_signal_artifacts,
)
from tests.candidate_factory import valid_candidate_payload


def _sample_result():
    return valid_candidate_payload(
        data_sources={"dc_concept_available": True},
        candidates=[
            {
                "ts_code": "000002.SZ",
                "name": "强热点",
                "score": 2.0,
                "relevance": 0.9,
                "source_topics": ["半导体"],
                "source_concepts": ["半导体设备"],
                "hotspot_feature_score": 0.8,
                "daily_confirm_score": 0.75,
                "confidence_score": 0.82,
                "confidence_label": "high",
            },
            {
                "ts_code": "000001.SZ",
                "name": "弱热点",
                "score": 1.0,
                "relevance": 0.3,
                "source_topics": ["半导体"],
                "source_concepts": ["半导体设备"],
            },
        ],
    )


def test_build_signal_frame_uses_canonical_columns_and_rank():
    frame = build_signal_frame(_sample_result(), model_version="test-model")

    for column in SIGNAL_COLUMNS:
        assert column in frame.columns
    assert frame["signal_date"].tolist() == ["20260629", "20260629"]
    assert frame["symbol"].tolist() == ["000002.SZ", "000001.SZ"]
    assert frame["rank"].tolist() == [1, 2]
    assert frame["model_version"].unique().tolist() == ["test-model"]
    assert pd.api.types.is_bool_dtype(frame["eligible_for_backtest"])
    assert frame["eligible_for_live"].eq(False).all()
    assert frame.loc[0, "daily_confirm_score"] == 0.75
    assert frame.loc[0, "confidence_label"] == "high"


def test_write_signal_artifacts_writes_parquet_csv_and_metadata(tmp_path):
    write_signal_artifacts(_sample_result(), tmp_path)

    assert (tmp_path / "signals.parquet").exists()
    assert (tmp_path / "signals.csv").exists()
    assert (tmp_path / "signals.meta.json").exists()
    meta = json.loads((tmp_path / "signals.meta.json").read_text(encoding="utf-8"))
    assert meta["contract"] == "alpha_research.signals"
    assert meta["rows"] == 2
    assert meta["data_cutoff"] == "20260629"
    assert meta["data_cutoff_semantics"] == "end_of_day"
    assert meta["execution_not_before"] == "next_trading_session"
    assert meta["future_data_included"] is False
    assert meta["artifact_role"] == "candidate_universe"
    assert meta["execution_eligible"] is False
    assert meta["evidence"]["out_of_sample_claim"] is False
    assert meta["evidence"]["temporal_context"] == "post_observation_generation"


def test_load_candidate_result_rejects_legacy_artifact(tmp_path):
    path = tmp_path / "candidate_universe.json"
    path.write_text(
        json.dumps(
            {
                "date": "2026-06-29",
                "candidate_universe": [{"ts_code": "000001.SZ"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CandidateContractError, match="schema_version"):
        load_candidate_result(path)
