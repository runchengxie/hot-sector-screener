from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from hot_sector_screener.data_sources.rotation_signal import load_industry_signal


def _write_signal(root: Path, run_name: str, signal_date: str, industry: str) -> None:
    run_dir = root / "strategy_outputs" / "etf_rotation_v3" / run_name
    run_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "signal_date": [signal_date],
            "industry": [industry],
            "rank": [1],
            "weight": [1.0],
        }
    ).to_csv(run_dir / "industry_signal.csv", index=False)


def test_load_industry_signal_selects_latest_signal_not_after_as_of(tmp_path, monkeypatch):
    _write_signal(tmp_path, "run-old", "20260618", "通信")
    _write_signal(tmp_path, "run-future", "20260620", "半导体")
    monkeypatch.setenv("DATA_PLATFORM_ROOT", str(tmp_path))

    frame = load_industry_signal(as_of_date="2026-06-19")

    assert frame["industry"].tolist() == ["通信"]
    assert frame["signal_date"].tolist() == ["20260618"]
    assert frame.attrs["as_of_date"] == "20260619"
    assert frame.attrs["signal_date"] == "20260618"
    assert frame.attrs["provenance_level"] == "signal_date_only"
    assert frame.attrs["strict_point_in_time"] is False
    assert frame.attrs["publisher_receipt_verified"] is False


def test_load_industry_signal_does_not_fall_forward_when_no_as_of_run(
    tmp_path,
    monkeypatch,
):
    _write_signal(tmp_path, "run-future", "20260620", "半导体")
    monkeypatch.setenv("DATA_PLATFORM_ROOT", str(tmp_path))

    frame = load_industry_signal(as_of_date="20260619")

    assert frame.empty
    assert frame.attrs["provenance_level"] == "unavailable"
    assert frame.attrs["strict_point_in_time"] is False
    assert frame.attrs["publisher_receipt_verified"] is False


def test_configured_rotation_run_is_still_validated_as_of(tmp_path, monkeypatch):
    _write_signal(tmp_path, "run-future", "20260620", "半导体")
    monkeypatch.setenv("DATA_PLATFORM_ROOT", str(tmp_path))

    frame = load_industry_signal(as_of_date="20260619", run_dir="run-future")

    assert frame.empty


def test_load_industry_signal_rejects_invalid_as_of_calendar_date(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PLATFORM_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="Invalid calendar date"):
        load_industry_signal(as_of_date="20269999")
