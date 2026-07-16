from __future__ import annotations

import pandas as pd

from hot_sector_screener.source_gate import build_source_gate, source_gate_issues
from hot_sector_screener.universe_builder import _data_source_status

DATE = "20260715"


def _frame(rows: int = 1, *, trade_date: str = DATE) -> pd.DataFrame:
    return pd.DataFrame({"trade_date": [trade_date] * rows, "value": range(rows)})


def _kpl_frame(rows: int = 1, *, trade_date: str = DATE) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [trade_date] * rows,
            "name": ["人工智能"] * rows,
            "con_code": [f"{index % 999999:06d}.SZ" for index in range(rows)],
            "con_name": [f"样例{index}" for index in range(rows)],
        }
    )


def _complete_dc(rows: int = 2) -> pd.DataFrame:
    frame = _frame(rows)
    frame.attrs.update(
        {
            "completeness": {
                "complete": True,
                "row_count": rows,
                "page_count": 1,
                "terminal_page_reached": True,
            },
            "completeness_verified": True,
        }
    )
    return frame


def _frames() -> dict[str, pd.DataFrame]:
    return {
        "ths_hot": pd.DataFrame(),
        "dc_concept": _frame(),
        "dc_concept_cons": _complete_dc(),
        "kpl_concept_cons": _kpl_frame(),
        "kpl_list": pd.DataFrame(),
        "limit_step": _frame(),
        "limit_cpt_list": pd.DataFrame(),
        "limit_list_ths": _frame(),
    }


def test_exact_kpl_and_two_same_day_events_are_normal() -> None:
    gate = build_source_gate(_frames(), DATE)

    assert gate["source_mode"] == "normal"
    assert gate["fallback_reason"] is None
    assert gate["mapping"]["kpl_complete"] is True
    assert gate["event_confirmation"]["sources"] == ["limit_list_ths", "limit_step"]


def test_capped_kpl_uses_complete_dc_fallback() -> None:
    frames = _frames()
    frames["kpl_concept_cons"] = _kpl_frame(3000)

    gate = build_source_gate(frames, DATE)

    assert gate["source_mode"] == "dc_fallback"
    assert gate["mapping"] == {"kpl_complete": False, "dc_complete": True}
    assert gate["sources"]["kpl_concept_cons"]["completeness_basis"] == "api_row_limit_reached"


def test_explicit_incomplete_kpl_receipt_overrides_below_limit_inference() -> None:
    frames = _frames()
    kpl = _kpl_frame()
    kpl.attrs.update(
        {
            "completeness": {
                "complete": False,
                "row_count": 1,
                "page_count": 1,
                "terminal_page_reached": True,
            },
            "completeness_verified": False,
        }
    )
    frames["kpl_concept_cons"] = kpl

    gate = build_source_gate(frames, DATE)

    assert gate["source_mode"] == "dc_fallback"
    assert gate["sources"]["kpl_concept_cons"]["complete"] is False
    assert gate["sources"]["kpl_concept_cons"]["completeness_basis"] == "manifest_incomplete"


def test_incomplete_dc_uses_explicit_event_fallback() -> None:
    frames = _frames()
    frames["kpl_concept_cons"] = pd.DataFrame()
    frames["dc_concept_cons"] = _frame(3000)

    gate = build_source_gate(frames, DATE)

    assert gate["source_mode"] == "event_fallback"
    assert gate["fallback_reason"] == "complete_member_mapping_unavailable"


def test_kpl_without_usable_mapping_keys_cannot_be_normal() -> None:
    frames = _frames()
    frames["kpl_concept_cons"] = _frame()

    gate = build_source_gate(frames, DATE)

    assert gate["source_mode"] == "dc_fallback"
    assert gate["mapping"]["kpl_complete"] is False
    assert gate["sources"]["kpl_concept_cons"]["completeness_basis"] == ("mapping_keys_invalid")
    assert gate["sources"]["kpl_concept_cons"]["mapping_keys"]["valid"] is False


def test_fewer_than_two_exact_date_event_sources_are_blocked() -> None:
    frames = _frames()
    frames["limit_step"] = pd.DataFrame()

    gate = build_source_gate(frames, DATE)

    assert gate["source_mode"] == "blocked"
    assert gate["fallback_reason"] == "insufficient_same_day_event_sources"


def test_previous_day_data_cannot_satisfy_exact_date_gate() -> None:
    frames = _frames()
    frames["kpl_concept_cons"] = _kpl_frame(trade_date="20260714")
    frames["dc_concept_cons"] = _frame(trade_date="20260714")

    gate = build_source_gate(frames, DATE)

    assert gate["sources"]["kpl_concept_cons"]["exact_date"] is False
    assert gate["sources"]["dc_concept_cons"]["exact_date"] is False
    assert gate["source_mode"] == "event_fallback"


def test_missing_or_malformed_trade_dates_fail_the_exact_date_gate() -> None:
    frames = _frames()
    frames["limit_step"] = pd.DataFrame(
        {"trade_date": [DATE, None, "not-a-date"], "value": [1, 2, 3]}
    )

    gate = build_source_gate(frames, DATE)

    status = gate["sources"]["limit_step"]
    assert status["exact_date"] is False
    assert status["invalid_trade_date_rows"] == 2
    assert gate["source_mode"] == "blocked"


def test_negative_receipt_is_unavailable_to_explicit_fixed_source_gate() -> None:
    frames = _frames()
    retained = _frame()
    retained.attrs["completeness"] = {
        "complete": False,
        "reason": "empty_refresh_receipt",
    }
    frames["kpl_concept_cons"] = retained
    gate = build_source_gate(frames, DATE)
    status = _data_source_status(
        {**frames, "industry_signal": pd.DataFrame()},
        gate,
    )

    assert gate["sources"]["kpl_concept_cons"]["last_known_good_retained"] is True
    assert status["kpl_concept_cons_available"] is False


def test_source_gate_metadata_detects_mode_tampering() -> None:
    gate = build_source_gate(_frames(), DATE)
    payload = {
        "observation_date": DATE,
        "source_mode": "event_fallback",
        "fallback_reason": "complete_member_mapping_unavailable",
        "source_gate": gate,
    }

    issues = source_gate_issues(payload)

    assert "source_gate.source_mode must match source_mode" in issues
    assert "source_gate.fallback_reason must match fallback_reason" in issues


def test_kpl_complete_metadata_requires_full_mapping_key_coverage() -> None:
    gate = build_source_gate(_frames(), DATE)
    gate["sources"]["kpl_concept_cons"]["mapping_keys"]["valid"] = False
    payload = {
        "observation_date": DATE,
        "source_mode": "normal",
        "fallback_reason": None,
        "source_gate": gate,
    }

    assert "kpl_complete requires fully populated KPL mapping keys" in source_gate_issues(payload)


def test_dc_complete_metadata_requires_exact_date_dc_concept() -> None:
    frames = _frames()
    frames["kpl_concept_cons"] = _frame(0)
    frames["dc_concept_cons"].attrs["completeness"] = {
        "complete": True,
        "row_count": len(frames["dc_concept_cons"]),
        "page_count": 1,
        "terminal_page_reached": True,
    }
    frames["dc_concept_cons"].attrs["completeness_verified"] = True
    gate = build_source_gate(frames, DATE)
    gate["sources"]["dc_concept"]["exact_date"] = False
    payload = {
        "observation_date": DATE,
        "source_mode": "dc_fallback",
        "fallback_reason": "kpl_concept_cons_exact_date_unavailable",
        "source_gate": gate,
    }

    assert "dc_complete requires exact-date dc_concept metadata" in source_gate_issues(payload)
