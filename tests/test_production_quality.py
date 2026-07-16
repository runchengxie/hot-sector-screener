from __future__ import annotations

import json
from typing import Any

import pandas as pd
import pytest

from hot_sector_screener.data_sources import platform
from hot_sector_screener.production_quality import (
    DEFAULT_REQUIRED_SOURCES,
    parse_source_list,
    validate_candidate_output,
)
from hot_sector_screener.source_gate import build_source_gate
from tests.candidate_factory import valid_candidate_payload


def _write_candidate_payload(path, *, size: int, source_value: bool = True) -> None:
    data_sources: dict[str, Any] = {
        f"{source}_available": source_value for source in DEFAULT_REQUIRED_SOURCES
    }
    candidates = [
        {
            "ts_code": f"00000{i}.SZ",
            "name": f"候选{i}",
            "score": 1.0,
            "relevance": 1.0,
            "source_topics": [],
            "source_concepts": [],
        }
        for i in range(size)
    ]
    payload = valid_candidate_payload(candidates=candidates, data_sources=data_sources)
    (path / "candidate_universe.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_parse_source_list_accepts_comma_separated_text():
    assert parse_source_list("ths_hot, daily ; dc_concept") == (
        "ths_hot",
        "daily",
        "dc_concept",
    )


def test_default_required_sources_keep_daily_optional_for_level_one():
    assert DEFAULT_REQUIRED_SOURCES == (
        "dc_concept",
        "dc_concept_cons",
        "kpl_concept_cons",
        "kpl_list",
        "limit_step",
        "limit_cpt_list",
        "limit_list_ths",
    )


def test_latest_common_date_uses_intersection(monkeypatch):
    dates_by_source = {
        "ths_hot": ["20260628", "20260629"],
        "dc_concept": ["20260629", "20260630"],
        "daily": ["20260629", "20260701"],
    }
    monkeypatch.setattr(platform, "list_available_dates", lambda source: dates_by_source[source])

    assert platform.latest_common_date(("ths_hot", "dc_concept", "daily")) == "20260629"


def test_validate_candidate_output_passes_for_ready_output(tmp_path):
    _write_candidate_payload(tmp_path, size=2)
    pd.DataFrame({"signal_date": ["20260629"], "symbol": ["000001.SZ"]}).to_parquet(
        tmp_path / "signals.parquet",
        index=False,
    )
    (tmp_path / "signals.meta.json").write_text("{}", encoding="utf-8")

    assert validate_candidate_output(tmp_path) == []


def test_validate_candidate_output_reports_missing_sources_and_empty_signals(tmp_path):
    _write_candidate_payload(tmp_path, size=0, source_value=False)
    pd.DataFrame({"signal_date": []}).to_parquet(tmp_path / "signals.parquet", index=False)

    issues = validate_candidate_output(tmp_path, required_sources=DEFAULT_REQUIRED_SOURCES)

    assert "required source unavailable: dc_concept" in issues
    assert "required source unavailable: limit_cpt_list" in issues
    assert "candidate count 0 is below min_candidates 2" in issues
    assert "signals.parquet is empty" in issues
    assert any(issue.startswith("missing signals.meta.json") for issue in issues)


def _gate_frame(date: str = "20260629") -> pd.DataFrame:
    return pd.DataFrame({"trade_date": [date]})


def _kpl_gate_frame(date: str = "20260629") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [date],
            "name": ["人工智能"],
            "con_code": ["000001.SZ"],
            "con_name": ["平安银行"],
        }
    )


def _write_capability_candidate(path, *, mode: str) -> None:
    empty = pd.DataFrame()
    dc_cons = _gate_frame()
    if mode == "dc_fallback":
        dc_cons.attrs.update(
            {
                "completeness": {
                    "complete": True,
                    "row_count": 1,
                    "page_count": 1,
                    "terminal_page_reached": True,
                },
                "completeness_verified": True,
            }
        )
    frames = {
        "ths_hot": empty,
        "dc_concept": _gate_frame(),
        "dc_concept_cons": dc_cons,
        "kpl_concept_cons": _kpl_gate_frame() if mode == "normal" else empty,
        "kpl_list": empty,
        "limit_step": _gate_frame() if mode != "blocked" else empty,
        "limit_cpt_list": empty,
        "limit_list_ths": _gate_frame(),
    }
    if mode == "event_fallback":
        frames["dc_concept_cons"] = _gate_frame()
    gate = build_source_gate(frames, "20260629")
    assert gate["source_mode"] == mode
    candidates = [
        {
            "ts_code": "000001.SZ",
            "name": "候选1",
            "score": 1.0,
            "relevance": 1.0,
            "source_topics": ["测试"],
            "source_concepts": ["测试"],
        },
        {
            "ts_code": "000002.SZ",
            "name": "候选2",
            "score": 0.9,
            "relevance": 0.9,
            "source_topics": ["测试"],
            "source_concepts": ["测试"],
        },
    ]
    payload = valid_candidate_payload(candidates=candidates)
    payload.update(
        source_mode=gate["source_mode"],
        fallback_reason=gate["fallback_reason"],
        source_gate=gate,
    )
    (path / "candidate_universe.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    pd.DataFrame({"signal_date": ["20260629"], "symbol": ["000001.SZ"]}).to_parquet(
        path / "signals.parquet", index=False
    )
    (path / "signals.meta.json").write_text("{}", encoding="utf-8")


@pytest.mark.parametrize("mode", ("normal", "dc_fallback", "event_fallback"))
def test_capability_gate_allows_three_delivery_modes(tmp_path, mode):
    _write_capability_candidate(tmp_path, mode=mode)

    assert validate_candidate_output(tmp_path) == []


def test_capability_gate_rejects_blocked_mode(tmp_path):
    _write_capability_candidate(tmp_path, mode="blocked")

    issues = validate_candidate_output(tmp_path)

    assert "source capability gate blocked: insufficient_same_day_event_sources" in issues
