from __future__ import annotations

import pandas as pd

from hot_sector_screener.ranking import apply_hotspot_feature_overlay


def test_hotspot_feature_overlay_boosts_stronger_hotspot_candidate():
    stocks = [
        {"ts_code": "000001.SZ", "name": "弱热点", "score": 1.0, "relevance": 1.0},
        {"ts_code": "000002.SZ", "name": "强热点", "score": 1.0, "relevance": 1.0},
    ]
    features = pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "hot_rank_pct": 0.1,
                "theme_strength_z": -1.0,
                "days_since_hot": 20,
                "failed_board_count_5d": 2,
            },
            {
                "symbol": "000002.SZ",
                "hot_rank_pct": 0.9,
                "theme_strength_z": 2.0,
                "days_since_hot": 1,
                "failed_board_count_5d": 0,
            },
        ]
    )

    ranked = apply_hotspot_feature_overlay(stocks, features, weight=0.25)

    assert ranked[0]["ts_code"] == "000002.SZ"
    assert ranked[0]["hotspot_feature_score"] > ranked[1]["hotspot_feature_score"]
    assert ranked[0]["score"] > ranked[1]["score"]


def test_hotspot_feature_overlay_leaves_missing_feature_rows_unchanged():
    stocks = [{"ts_code": "000001.SZ", "score": 1.0, "relevance": 0.8}]
    features = pd.DataFrame([{"symbol": "000002.SZ", "hot_rank_pct": 0.9}])

    ranked = apply_hotspot_feature_overlay(stocks, features)

    assert ranked == stocks
