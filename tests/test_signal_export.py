from __future__ import annotations

import json

import pandas as pd

from hot_sector_screener.signal_export import (
    SIGNAL_COLUMNS,
    build_signal_frame,
    write_signal_artifacts,
)


def _sample_result():
    return {
        "date": "2026-06-29",
        "date_int": "20260629",
        "generated_at": "2026-06-30T13:48:57",
        "universe_size": 2,
        "data_sources": {"dc_concept_available": True},
        "config_snapshot": {"max_candidates": 100},
        "candidate_universe": [
            {
                "ts_code": "000002.SZ",
                "name": "强热点",
                "score": 2.0,
                "relevance": 0.9,
                "source_topics": ["半导体"],
                "source_concepts": ["半导体设备"],
                "hotspot_feature_score": 0.8,
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
    }


def test_build_signal_frame_uses_canonical_columns_and_rank():
    frame = build_signal_frame(_sample_result(), model_version="test-model")

    for column in SIGNAL_COLUMNS:
        assert column in frame.columns
    assert frame["signal_date"].tolist() == ["20260629", "20260629"]
    assert frame["symbol"].tolist() == ["000002.SZ", "000001.SZ"]
    assert frame["rank"].tolist() == [1, 2]
    assert frame["model_version"].unique().tolist() == ["test-model"]
    assert pd.api.types.is_bool_dtype(frame["eligible_for_backtest"])


def test_write_signal_artifacts_writes_parquet_csv_and_metadata(tmp_path):
    write_signal_artifacts(_sample_result(), tmp_path)

    assert (tmp_path / "signals.parquet").exists()
    assert (tmp_path / "signals.csv").exists()
    assert (tmp_path / "signals.meta.json").exists()
    meta = json.loads((tmp_path / "signals.meta.json").read_text(encoding="utf-8"))
    assert meta["contract"] == "cstree.signals"
    assert meta["rows"] == 2
