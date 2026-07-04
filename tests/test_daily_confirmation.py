from __future__ import annotations

import pandas as pd

from hot_sector_screener.confidence import apply_candidate_confidence
from hot_sector_screener.daily_confirmation import (
    apply_daily_confirmation_overlay,
    build_daily_confirmation,
)
from hot_sector_screener.outcome_evaluation import build_candidate_outcome_report


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": f"2026062{idx}",
                "close": close,
                "high": close * 1.02,
                "low": close * 0.98,
                "amount": amount,
                "pct_chg": 1.0,
            }
            for idx, (close, amount) in enumerate(
                [
                    (10.0, 1000.0),
                    (10.2, 1100.0),
                    (10.5, 1200.0),
                    (10.8, 1300.0),
                    (11.0, 1500.0),
                    (11.4, 1800.0),
                ]
            )
        ]
    )


def test_daily_confirmation_scores_and_overlays_candidates():
    features = build_daily_confirmation(_history())

    assert features["ts_code"].tolist() == ["000001.SZ"]
    assert 0.0 <= features.loc[0, "daily_confirm_score"] <= 1.0

    stocks = [{"ts_code": "000001.SZ", "score": 1.0, "relevance": 1.0}]
    overlaid = apply_daily_confirmation_overlay(stocks, _history(), weight=0.2)

    assert overlaid[0]["daily_confirm_score"] == features.loc[0, "daily_confirm_score"]
    assert overlaid[0]["pre_daily_score"] == 1.0
    assert "trend_score" in overlaid[0]


def test_candidate_confidence_adds_label_and_components():
    stocks = [
        {
            "ts_code": "000001.SZ",
            "score": 1.0,
            "relevance": 0.9,
            "source_topics": ["AI", "半导体"],
            "source_concepts": ["AI", "芯片"],
            "daily_confirm_score": 0.8,
            "hotspot_feature_score": 0.7,
            "liquidity_score": 0.9,
        }
    ]

    scored = apply_candidate_confidence(stocks)

    assert scored[0]["confidence_label"] in {"high", "medium", "watch"}
    assert 0.0 <= scored[0]["confidence_score"] <= 1.0
    assert scored[0]["confidence_components"]["daily_confirm"] == 0.8


def test_candidate_outcome_report_summarises_future_daily_bars():
    candidates = [{"ts_code": "000001.SZ"}]
    base_daily = pd.DataFrame([{"ts_code": "000001.SZ", "close": 10.0, "high": 10.2}])
    future_daily = [
        pd.DataFrame([{"ts_code": "000001.SZ", "close": 10.5, "high": 11.0}]),
        pd.DataFrame([{"ts_code": "000001.SZ", "close": 10.8, "high": 11.2}]),
        pd.DataFrame([{"ts_code": "000001.SZ", "close": 10.2, "high": 10.9}]),
    ]

    report = build_candidate_outcome_report(candidates, base_daily, future_daily)

    assert report["available"] is True
    assert report["horizons"]["t_plus_1"]["close_return"]["mean_pct"] == 5.0
    assert report["horizons"]["t_plus_3"]["next_high_return"]["top_pct"] == 12.0
