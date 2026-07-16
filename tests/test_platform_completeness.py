from __future__ import annotations

import pandas as pd
import yaml

from hot_sector_screener.data_sources.platform import load_dc_concept_cons
from hot_sector_screener.source_gate import frame_source_status


def _write_dc_partition(tmp_path, *, manifest_row_count: int, complete: bool = True):
    dataset = (
        tmp_path
        / "assets"
        / "tushare"
        / "a_share"
        / "dc_concept_cons"
        / "a_share_all_dc_concept_cons_latest"
    )
    partition = dataset / "data" / "trade_date=20260715"
    partition.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "theme_code": "TEST",
                "trade_date": "20260715",
                "industry": "银行",
                "hot_num": 1,
            },
            {
                "ts_code": "000002.SZ",
                "name": "万科A",
                "theme_code": "TEST",
                "trade_date": "20260715",
                "industry": "地产",
                "hot_num": 2,
            },
        ]
    ).to_parquet(partition / "part-000.parquet", index=False)
    manifest = {
        "completeness": {
            "complete": complete,
            "trade_dates": {
                "20260715": {
                    "complete": complete,
                    "row_count": manifest_row_count,
                    "page_count": 1,
                    "page_size": 3000,
                    "terminal_page_reached": True,
                    "distinct_theme_count": 1,
                    "coverage": {
                        "field": "theme_code",
                        "populated_row_count": 2,
                        "row_coverage_ratio": 1.0,
                    },
                }
            },
        }
    }
    (dataset / "manifest.yml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    return dataset


def test_loader_attaches_verified_exact_date_completeness(tmp_path, monkeypatch) -> None:
    _write_dc_partition(tmp_path, manifest_row_count=2)
    monkeypatch.setenv("DATA_PLATFORM_ROOT", str(tmp_path))

    frame = load_dc_concept_cons("2026-07-15")

    assert len(frame) == 2
    assert frame.attrs["requested_trade_date"] == "20260715"
    assert frame.attrs["observed_trade_dates"] == ["20260715"]
    assert frame.attrs["completeness"]["complete"] is True
    assert frame.attrs["completeness_verified"] is True


def test_loader_rejects_manifest_whose_row_count_does_not_match_partition(
    tmp_path, monkeypatch
) -> None:
    _write_dc_partition(tmp_path, manifest_row_count=3000)
    monkeypatch.setenv("DATA_PLATFORM_ROOT", str(tmp_path))

    frame = load_dc_concept_cons("20260715")

    assert len(frame) == 2
    assert frame.attrs["completeness_verified"] is False


def test_loader_rejects_dc_receipt_without_full_theme_coverage(tmp_path, monkeypatch) -> None:
    dataset = _write_dc_partition(tmp_path, manifest_row_count=2)
    manifest_path = dataset / "manifest.yml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    receipt = manifest["completeness"]["trade_dates"]["20260715"]
    receipt["coverage"]["populated_row_count"] = 1
    receipt["coverage"]["row_coverage_ratio"] = 0.5
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("DATA_PLATFORM_ROOT", str(tmp_path))

    frame = load_dc_concept_cons("20260715")

    assert len(frame) == 2
    assert frame.attrs["completeness_verified"] is False


def test_legacy_empty_refresh_receipt_overrides_retained_partition(tmp_path, monkeypatch) -> None:
    dataset = _write_dc_partition(tmp_path, manifest_row_count=2)
    legacy_empty = {
        "query": {"start_date": "20260715", "end_date": "20260715"},
        "totals": {"trade_dates_empty": 1},
        "written_trade_dates": [],
        "empty_trade_dates": ["20260715"],
    }
    (dataset / "manifest.yml").write_text(
        yaml.safe_dump(legacy_empty, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("DATA_PLATFORM_ROOT", str(tmp_path))

    frame = load_dc_concept_cons("20260715")

    assert len(frame) == 2
    assert frame.attrs["completeness"]["reason"] == "empty_refresh_receipt"
    assert frame.attrs["completeness_verified"] is False
    status = frame_source_status(frame, "20260715")
    assert status["available"] is False
    assert status["exact_date"] is False
    assert status["complete"] is False
    assert status["last_known_good_retained"] is True
