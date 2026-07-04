from __future__ import annotations

import json

import pandas as pd

from hot_sector_screener.data_sources import platform
from hot_sector_screener.production_quality import (
    DEFAULT_REQUIRED_SOURCES,
    parse_source_list,
    validate_candidate_output,
)


def _write_candidate_payload(path, *, size: int, source_value: bool = True) -> None:
    data_sources = {f"{source}_available": source_value for source in DEFAULT_REQUIRED_SOURCES}
    payload = {
        "date": "2026-06-29",
        "date_int": "20260629",
        "candidate_universe": [{"ts_code": f"00000{i}.SZ", "relevance": 1.0} for i in range(size)],
        "universe_size": size,
        "config_snapshot": {"min_candidates": 2},
        "data_sources": data_sources,
    }
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
        "ths_hot",
        "dc_concept",
        "dc_concept_cons",
        "kpl_concept_cons",
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

    issues = validate_candidate_output(tmp_path)

    assert "required source unavailable: ths_hot" in issues
    assert "candidate count 0 is below min_candidates 2" in issues
    assert "signals.parquet is empty" in issues
    assert any(issue.startswith("missing signals.meta.json") for issue in issues)
