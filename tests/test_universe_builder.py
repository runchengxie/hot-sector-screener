"""Tests for the universe builder (Screener pipeline)."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from hot_sector_screener.universe_builder import Screener


@pytest.fixture
def sample_topics():
    return [
        {
            "topic": "AI医疗",
            "weight": 0.32,
            "reasoning": "测试主题",
            "related_concepts": ["AI医疗"],
            "source_signals": ["ths_hot"],
        },
        {
            "topic": "半导体",
            "weight": 0.25,
            "reasoning": "测试主题",
            "related_concepts": ["半导体"],
            "source_signals": ["dc_concept"],
        },
    ]


@pytest.fixture
def empty_df():
    return pd.DataFrame()


@pytest.fixture
def sample_dc_cons():
    return pd.DataFrame(
        [
            {
                "ts_code": "300308.SZ",
                "name": "中际旭创",
                "theme_code": "CPO",
                "trade_date": "20260619",
                "industry": "通信",
                "hot_num": 5,
            },
        ]
    )


class TestScreenerInit:
    def test_default_config(self):
        screener = Screener()
        assert screener.max_candidates == 100
        assert screener.min_candidates == 30
        assert screener.topics_per_run == 5
        assert screener.stocks_per_topic == 25

    def test_custom_config(self):
        config = {
            "universe": {
                "max_candidates": 50,
                "min_candidates": 20,
                "topics_per_run": 3,
                "stocks_per_topic": 10,
            },
            "llm": {"enabled": False},
        }
        screener = Screener(config)
        assert screener.max_candidates == 50
        assert screener.min_candidates == 20
        assert screener.topics_per_run == 3
        assert screener.stocks_per_topic == 10
        assert screener.classifier.enabled is False

    def test_partial_config(self):
        config = {"universe": {"max_candidates": 80}}
        screener = Screener(config)
        assert screener.max_candidates == 80
        # defaults preserved for unspecified keys
        assert screener.min_candidates == 30
        assert screener.stocks_per_topic == 25


class TestScreenerBuildUniverse:
    """Tests for Screener.build_universe() with mocked data sources."""

    def test_with_pre_classified_topics(self, sample_topics, sample_dc_cons):
        """Given pre-classified topics, build_universe should skip LLM and produce output."""
        with (
            patch("hot_sector_screener.universe_builder.load_ths_hot") as mock_ths,
            patch("hot_sector_screener.universe_builder.load_dc_concept") as mock_dc,
            patch("hot_sector_screener.universe_builder.load_dc_concept_cons") as mock_dc_cons,
            patch("hot_sector_screener.universe_builder.load_kpl_concept_cons") as mock_kpl,
            patch("hot_sector_screener.universe_builder.load_hotspot_features") as mock_hf,
            patch("hot_sector_screener.universe_builder.load_industry_signal") as mock_ind,
            patch("hot_sector_screener.universe_builder.write_signal_artifacts") as mock_signals,
        ):
            mock_ths.return_value = pd.DataFrame()
            mock_dc.return_value = pd.DataFrame()
            mock_dc_cons.return_value = sample_dc_cons
            mock_kpl.return_value = pd.DataFrame()
            mock_hf.return_value = pd.DataFrame()
            mock_ind.return_value = pd.DataFrame()
            mock_signals.return_value = {"parquet": "signals.parquet"}

            screener = Screener()
            result = screener.build_universe(
                trade_date="2026-06-19",
                topics=sample_topics,
            )

            assert result["date"] == "2026-06-19"
            assert result["topics"] == sample_topics
            assert "candidate_universe" in result
            assert "universe_size" in result
            assert "config_snapshot" in result
            assert "data_sources" in result
            assert result["data_sources"]["dc_concept_cons_available"] is True
            assert result["data_sources"]["hotspot_features_available"] is False

    def test_empty_data_returns_empty_universe(self):
        """When all data sources return empty, universe should still produce a valid result."""
        with (
            patch("hot_sector_screener.universe_builder.load_ths_hot", return_value=pd.DataFrame()),
            patch(
                "hot_sector_screener.universe_builder.load_dc_concept", return_value=pd.DataFrame()
            ),
            patch(
                "hot_sector_screener.universe_builder.load_dc_concept_cons",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_kpl_concept_cons",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_hotspot_features",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_industry_signal",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.write_signal_artifacts",
                return_value={},
            ),
        ):
            screener = Screener({"llm": {"enabled": False}})
            result = screener.build_universe(trade_date="2026-06-19")

            assert result["date"] == "2026-06-19"
            assert isinstance(result["universe_size"], int)
            assert "generated_at" in result

    def test_output_dir_structure(self, sample_topics, sample_dc_cons, tmp_path):
        """build_universe should create output files when output_dir is specified."""
        with (
            patch("hot_sector_screener.universe_builder.load_ths_hot", return_value=pd.DataFrame()),
            patch(
                "hot_sector_screener.universe_builder.load_dc_concept", return_value=pd.DataFrame()
            ),
            patch(
                "hot_sector_screener.universe_builder.load_dc_concept_cons",
                return_value=sample_dc_cons,
            ),
            patch(
                "hot_sector_screener.universe_builder.load_kpl_concept_cons",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_hotspot_features",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_industry_signal",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.write_signal_artifacts",
                return_value={"parquet": "signals.parquet"},
            ),
            patch(
                "hot_sector_screener.universe_builder.load_daily_data",
                return_value=pd.DataFrame(
                    [
                        {
                            "ts_code": "300308.SZ",
                            "close": 100,
                            "amount": 100000,
                            "high": 102,
                            "low": 99,
                            "pct_chg": 1.0,
                        }
                    ]
                ),
            ),
            patch(
                "hot_sector_screener.universe_builder.list_available_dates",
                return_value=["20260619", "20260620"],
            ),
        ):
            screener = Screener()
            result = screener.build_universe(
                trade_date="2026-06-19",
                output_dir=str(tmp_path),
                topics=sample_topics,
            )

            # Check output files exist
            assert (tmp_path / "candidate_universe.json").exists()
            assert (tmp_path / "run_config.json").exists()
            assert (tmp_path / "lineage.json").exists()
            assert (tmp_path / "candidate_quality.json").exists()
            assert "quality_report" in result
            assert result["output_dir"] == str(tmp_path)


class TestScreenerScan:
    def test_scan_returns_data_overview(self):
        """scan should return structured data even when data is empty."""
        with (
            patch("hot_sector_screener.universe_builder.load_ths_hot", return_value=pd.DataFrame()),
            patch(
                "hot_sector_screener.universe_builder.load_dc_concept", return_value=pd.DataFrame()
            ),
            patch(
                "hot_sector_screener.universe_builder.load_dc_concept_cons",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_kpl_concept_cons",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_hotspot_features",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_industry_signal",
                return_value=pd.DataFrame(),
            ),
        ):
            screener = Screener()
            result = screener.scan(trade_date="2026-06-19")

            assert result["date"] == "2026-06-19"
            assert "ths_hot" in result
            assert "dc_concept" in result
            assert "dc_concept_cons" in result
            assert "kpl_concept_cons" in result
            assert "hotspot_features" in result
            assert "industry_signal" in result
            assert result["ths_hot"]["rows"] == 0


class TestScreenerBuildPrompt:
    def test_build_prompt_returns_prompt_text(self):
        with (
            patch("hot_sector_screener.universe_builder.load_ths_hot", return_value=pd.DataFrame()),
            patch(
                "hot_sector_screener.universe_builder.load_dc_concept", return_value=pd.DataFrame()
            ),
            patch(
                "hot_sector_screener.universe_builder.load_industry_signal",
                return_value=pd.DataFrame(),
            ),
        ):
            screener = Screener()
            result = screener.build_prompt(trade_date="2026-06-19")

            assert "date" in result
            assert "date_int" in result
            assert result["date_int"] == "20260619"
            assert "prompt" in result
            assert result["prompt_length"] > 0
            assert "stock_count" in result
            assert "concept_count" in result
            assert result["industry_signal_available"] is False
