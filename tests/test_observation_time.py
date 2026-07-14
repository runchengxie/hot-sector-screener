from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from hot_sector_screener import observation_time

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_default_uses_latest_common_completed_date(monkeypatch):
    calls = []

    def fake_latest(sources, not_after):
        calls.append((sources, not_after))
        return "20260714"

    monkeypatch.setattr(observation_time, "_latest_common_date", fake_latest)

    resolved = observation_time.resolve_observation_date(
        None,
        now=datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI),
    )

    assert resolved == "20260714"
    assert calls[0][1] == "20260714"


def test_same_day_is_rejected_before_observation_complete_time():
    with pytest.raises(ValueError, match="not complete"):
        observation_time.resolve_observation_date(
            "20260715",
            now=datetime(2026, 7, 15, 15, 59, tzinfo=SHANGHAI),
        )


def test_same_day_is_allowed_after_observation_complete_time():
    assert (
        observation_time.resolve_observation_date(
            "2026-07-15",
            now=datetime(2026, 7, 15, 16, 0, tzinfo=SHANGHAI),
        )
        == "20260715"
    )


@pytest.mark.parametrize("value", ["20260716", "20269999", "2026-02-30", "2026071"])
def test_future_and_invalid_dates_are_rejected(value):
    with pytest.raises(ValueError):
        observation_time.resolve_observation_date(
            value,
            now=datetime(2026, 7, 15, 17, 0, tzinfo=SHANGHAI),
        )
