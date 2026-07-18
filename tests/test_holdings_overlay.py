from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

import pandas as pd
import pytest

from hot_sector_screener import cli
from hot_sector_screener.cli import build_parser
from hot_sector_screener.holdings_contract import (
    HOLDINGS_OVERLAY_ARTIFACT_TYPE,
    HOLDINGS_OVERLAY_SCHEMA_VERSION,
    HOLDINGS_SNAPSHOT_ARTIFACT_TYPE,
    HOLDINGS_SNAPSHOT_SCHEMA_VERSION,
    HoldingsOverlayContractError,
    canonical_sha256,
    holdings_feature_policy,
    validate_holdings_overlay,
    validate_holdings_snapshot,
)
from hot_sector_screener.holdings_overlay import build_holdings_overlay
from tests.candidate_factory import valid_candidate_payload

OBSERVATION_DATE = "20260629"


def _snapshot(*symbols: str, as_of_date: str = OBSERVATION_DATE) -> dict[str, object]:
    return {
        "schema_version": HOLDINGS_SNAPSHOT_SCHEMA_VERSION,
        "artifact_type": HOLDINGS_SNAPSHOT_ARTIFACT_TYPE,
        "market": "CN",
        "as_of_date": as_of_date,
        "symbols": list(symbols),
    }


def _candidate_result() -> dict[str, Any]:
    return valid_candidate_payload(
        candidates=[
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "score": 0.8,
                "relevance": 0.8,
                "source_topics": ["金融科技"],
                "source_concepts": ["金融科技"],
            }
        ]
    )


def _theme_rows(*symbols: str) -> list[dict[str, object]]:
    return [
        {
            "ts_code": symbol,
            "name": "平安银行" if symbol == "000001.SZ" else "万科A",
            "score": 0.8 - index * 0.1,
            "relevance": 0.8 - index * 0.1,
            "source_topics": ["金融科技"],
            "source_concepts": ["金融科技"],
        }
        for index, symbol in enumerate(symbols)
    ]


def _daily_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "trade_date": OBSERVATION_DATE,
                "close": 12.0,
                "high": 12.2,
                "low": 11.8,
                "pct_chg": 1.0,
                "amount": 300.0,
            },
            {
                "ts_code": "000002.SZ",
                "name": "万科A",
                "trade_date": OBSERVATION_DATE,
                "close": 8.0,
                "high": 8.2,
                "low": 7.9,
                "pct_chg": 0.5,
                "amount": 200.0,
            },
            {
                "ts_code": "000003.SZ",
                "name": "国华网安",
                "trade_date": OBSERVATION_DATE,
                "close": 15.0,
                "high": 15.2,
                "low": 14.8,
                "pct_chg": -0.5,
                "amount": 100.0,
            },
        ]
    )


def _history(*, stale_symbol: str | None = None) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for symbol, base in (("000001.SZ", 12.0), ("000002.SZ", 8.0)):
        dates = ["20260627", "20260628", OBSERVATION_DATE]
        if symbol == stale_symbol:
            dates = ["20260626", "20260627", "20260628"]
        for index, trade_date in enumerate(dates):
            close = base + index * 0.1
            rows.append(
                {
                    "ts_code": symbol,
                    "trade_date": trade_date,
                    "close": close,
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "pct_chg": 1.0,
                    "amount": 100.0 + index * 10,
                }
            )
    return pd.DataFrame(rows)


def _build(
    *,
    themes: tuple[str, ...] = ("000001.SZ",),
    stale_symbol: str | None = None,
    min_amount_rank_pct: float = 80.0,
) -> dict[str, Any]:
    return build_holdings_overlay(
        candidate_result=_candidate_result(),
        current_theme_candidates=_theme_rows(*themes),
        holdings_snapshot=_snapshot("000002.SZ"),
        daily_df=_daily_rows(),
        daily_history=_history(stale_symbol=stale_symbol),
        min_amount_rank_pct=min_amount_rank_pct,
        min_price=2.0,
        max_price=200.0,
        allow_st=False,
    )


def test_feature_policy_hash_is_canonical():
    policy = holdings_feature_policy()
    digest = policy.pop("canonical_sha256")
    encoded = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert digest == hashlib.sha256(encoded).hexdigest()


def test_snapshot_contract_is_strict_and_rejects_future_or_duplicates():
    assert validate_holdings_snapshot(_snapshot("000001.SZ"), observation_date=OBSERVATION_DATE)[
        "symbols"
    ] == ["000001.SZ"]

    future = _snapshot("000001.SZ", as_of_date="20260630")
    with pytest.raises(HoldingsOverlayContractError, match="must not follow"):
        validate_holdings_snapshot(future, observation_date=OBSERVATION_DATE)

    duplicated = _snapshot("000001.SZ", "000001.SZ")
    with pytest.raises(HoldingsOverlayContractError, match="duplicated"):
        validate_holdings_snapshot(duplicated, observation_date=OBSERVATION_DATE)

    with_extra = {**_snapshot("000001.SZ"), "strategy": "top10"}
    with pytest.raises(HoldingsOverlayContractError, match="exactly"):
        validate_holdings_snapshot(with_extra, observation_date=OBSERVATION_DATE)


def test_overlay_rescores_declared_holdings_without_inventing_theme_history():
    overlay = _build()
    assert overlay["schema_version"] == HOLDINGS_OVERLAY_SCHEMA_VERSION
    assert overlay["artifact_type"] == HOLDINGS_OVERLAY_ARTIFACT_TYPE
    assert overlay["eligibility_parameters"] == {
        "entry_min_amount_rank_pct": 80.0,
        "min_price": 2.0,
        "max_price": 200.0,
        "allow_st": False,
    }
    assert validate_holdings_overlay(overlay) is overlay

    rows = {row["ts_code"]: row for row in overlay["rows"]}
    incumbent = rows["000002.SZ"]
    assert incumbent["is_current_holding"] is True
    assert incumbent["current_theme_match"] is False
    assert incumbent["theme_score"] == 0.0
    assert incumbent["theme_relevance"] == 0.0
    assert incumbent["source_topics"] == []
    assert incumbent["source_concepts"] == []
    assert incumbent["last_theme_seen"] is None
    assert incumbent["theme_age"] is None
    assert incumbent["entry_eligible"] is False
    assert incumbent["hold_eligible"] is True
    assert incumbent["technical_as_of_date"] == OBSERVATION_DATE
    assert incumbent["amount_rank_pct"] == 66.7

    current = rows["000001.SZ"]
    assert current["current_theme_match"] is True
    assert current["last_theme_seen"] == OBSERVATION_DATE
    assert current["theme_age"] == 0
    assert current["entry_eligible"] is True


def test_stale_technical_history_is_not_forward_filled():
    overlay = _build(stale_symbol="000002.SZ")
    incumbent = next(row for row in overlay["rows"] if row["ts_code"] == "000002.SZ")

    assert incumbent["technical_as_of_date"] is None
    assert incumbent["technical_history_days"] is None
    assert incumbent["daily_confirm_score"] is None
    assert incumbent["hold_eligible"] is False
    assert "current_day_technical_unavailable" in incumbent["hold_ineligible_reasons"]


def test_entry_liquidity_gate_does_not_turn_into_a_hold_decision():
    overlay = _build(themes=("000001.SZ", "000002.SZ"))
    incumbent = next(row for row in overlay["rows"] if row["ts_code"] == "000002.SZ")

    assert incumbent["current_theme_match"] is True
    assert incumbent["entry_eligible"] is False
    assert incumbent["hold_eligible"] is True
    assert incumbent["entry_ineligible_reasons"] == ["entry_liquidity_below_threshold"]
    assert incumbent["hold_ineligible_reasons"] == []


def test_validator_rejects_theme_values_on_an_unmatched_holding():
    overlay = _build()
    incumbent = next(row for row in overlay["rows"] if row["ts_code"] == "000002.SZ")
    incumbent["theme_score"] = 0.4

    with pytest.raises(HoldingsOverlayContractError, match="unmatched theme fields"):
        validate_holdings_overlay(overlay)


def test_overlay_contract_rejects_unversioned_extra_root_fields():
    overlay = _build()
    overlay["strategy"] = "top10"

    with pytest.raises(HoldingsOverlayContractError, match="overlay fields are invalid"):
        validate_holdings_overlay(overlay)


def test_run_parser_accepts_versioned_holdings_snapshot_path():
    args = build_parser().parse_args(
        ["run", "--date", "20260629", "--holdings", "holdings.json", "--no-llm"]
    )

    assert args.holdings == "holdings.json"


def test_validate_overlay_cli_prints_owner_canonical_summary(tmp_path, capsys):
    overlay = _build()
    input_path = tmp_path / "holdings_eligibility_overlay.json"
    input_path.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")

    cli.cmd_validate_holdings_overlay(argparse.Namespace(input=str(input_path)))

    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "valid": True,
        "artifact_type": HOLDINGS_OVERLAY_ARTIFACT_TYPE,
        "schema_version": HOLDINGS_OVERLAY_SCHEMA_VERSION,
        "policy_id": "hotsector.holdings_overlay.daily_rescore",
        "policy_version": "1.0.0",
        "policy_sha256": overlay["feature_policy"]["canonical_sha256"],
        "observation_date": OBSERVATION_DATE,
        "candidate_artifact_type": overlay["candidate_artifact_type"],
        "candidate_schema_version": overlay["candidate_schema_version"],
        "candidate_payload_sha256": overlay["candidate_payload_sha256"],
        "row_count": 2,
        "incumbent_count": 1,
        "current_theme_match_count": 1,
        "entry_eligible_count": 1,
        "hold_eligible_count": 2,
        "canonical_sha256": canonical_sha256(overlay),
    }
