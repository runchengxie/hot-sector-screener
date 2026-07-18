"""Tests for the universe builder (Screener pipeline)."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from hot_sector_screener import universe_builder
from hot_sector_screener.candidate_contract import validate_candidate_result
from hot_sector_screener.holdings_contract import validate_holdings_overlay
from hot_sector_screener.topic_classifier import TopicValidationError
from hot_sector_screener.topic_provider import ProviderReceipt, ProviderResponse
from hot_sector_screener.universe_builder import Screener


@pytest.fixture
def sample_topics():
    return [
        {
            "topic": "AI医疗",
            "weight": 0.32,
            "reasoning": "测试主题",
            "related_concepts": ["AI医疗"],
            "source_signals": ["dc_concept"],
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


def _install_contract_path_loaders(monkeypatch) -> None:
    frames = {
        "load_ths_hot": pd.DataFrame(
            [
                {
                    "ts_code": "000977.SZ",
                    "ts_name": "浪潮信息",
                    "concept": '["AI算力"]',
                }
            ]
        ),
        "load_dc_concept": pd.DataFrame(
            [
                {"name": "CPO概念", "theme_code": "CPO"},
                {"name": "AI算力", "theme_code": "AI_COMPUTE"},
            ]
        ),
        "load_dc_concept_cons": pd.DataFrame(
            [
                {
                    "ts_code": "300308.SZ",
                    "name": "中际旭创",
                    "theme_code": "CPO",
                }
            ]
        ),
        "load_kpl_concept_cons": pd.DataFrame(
            [
                {
                    "ts_code": "000025.KP",
                    "name": "AI算力",
                    "con_name": "浪潮信息",
                    "con_code": "000977.SZ",
                }
            ]
        ),
        "load_kpl_list": pd.DataFrame(
            [
                {
                    "ts_code": "600990.SH",
                    "name": "四创电子",
                    "theme": "商业航天",
                    "tag": "涨停",
                }
            ]
        ),
    }
    empty_loaders = (
        "load_limit_step",
        "load_limit_cpt_list",
        "load_limit_list_ths",
        "load_hotspot_features",
        "load_industry_signal",
        "load_daily_data",
        "load_daily_history",
    )
    frames.update({name: pd.DataFrame() for name in empty_loaders})
    for loader_name, frame in frames.items():
        monkeypatch.setattr(
            universe_builder,
            loader_name,
            lambda *args, _frame=frame, **kwargs: _frame.copy(),
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

    @pytest.mark.parametrize("legacy_field", ["model", "provider"])
    def test_direct_legacy_llm_config_is_rejected(self, legacy_field):
        with pytest.raises(ValueError, match=legacy_field):
            Screener({"llm": {legacy_field: "deployment-specific"}})


class TestScreenerBuildUniverse:
    """Tests for Screener.build_universe() with mocked data sources."""

    def test_with_pre_classified_topics(self, sample_topics, sample_dc_cons):
        """Given pre-classified topics, build_universe should skip LLM and produce output."""
        with (
            patch("hot_sector_screener.universe_builder.load_ths_hot") as mock_ths,
            patch("hot_sector_screener.universe_builder.load_dc_concept") as mock_dc,
            patch("hot_sector_screener.universe_builder.load_dc_concept_cons") as mock_dc_cons,
            patch("hot_sector_screener.universe_builder.load_kpl_concept_cons") as mock_kpl,
            patch(
                "hot_sector_screener.universe_builder.load_kpl_list",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_step",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_cpt_list",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_list_ths",
                return_value=pd.DataFrame(),
            ),
            patch("hot_sector_screener.universe_builder.load_hotspot_features") as mock_hf,
            patch("hot_sector_screener.universe_builder.load_industry_signal") as mock_ind,
            patch(
                "hot_sector_screener.universe_builder.load_daily_history",
                return_value=pd.DataFrame(),
            ),
            patch("hot_sector_screener.universe_builder.write_signal_artifacts") as mock_signals,
        ):
            mock_ths.return_value = pd.DataFrame()
            mock_dc.return_value = pd.DataFrame(
                [
                    {"name": "AI医疗", "theme_code": "AI_HEALTH"},
                    {"name": "半导体", "theme_code": "SEMICONDUCTOR"},
                ]
            )
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
            assert result["schema_version"] == "2.0.0"
            assert result["model_identity"]["model_id"] == "hotsector-theme-v3"
            assert (
                result["source_concepts_policy"]["policy_id"]
                == "hotsector.source_concepts.theme_only"
            )
            assert result["topics"] == sample_topics
            assert "candidate_universe" in result
            assert "universe_size" in result
            assert "config_snapshot" in result
            assert "data_sources" in result
            assert result["data_sources"]["dc_concept_cons_available"] is True
            assert result["data_sources"]["hotspot_features_available"] is False
            assert result["data_cutoff"] == "20260619"
            generated_at = datetime.fromisoformat(result["generated_at"])
            assert generated_at.utcoffset() is not None
            mock_ind.assert_called_once_with(as_of_date="20260619", run_dir=None)

    def test_empty_data_returns_empty_universe(self):
        """When all data sources return empty, universe should still produce a valid result."""
        with (
            patch("hot_sector_screener.universe_builder.load_ths_hot", return_value=pd.DataFrame()),
            patch(
                "hot_sector_screener.universe_builder.load_dc_concept",
                return_value=pd.DataFrame(),
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
                "hot_sector_screener.universe_builder.load_kpl_list",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_step",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_cpt_list",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_list_ths",
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
                "hot_sector_screener.universe_builder.load_daily_history",
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
                "hot_sector_screener.universe_builder.load_dc_concept",
                return_value=pd.DataFrame(
                    [
                        {"name": "AI医疗", "theme_code": "AI_HEALTH"},
                        {"name": "半导体", "theme_code": "SEMICONDUCTOR"},
                    ]
                ),
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
                "hot_sector_screener.universe_builder.load_kpl_list",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_step",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_cpt_list",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_list_ths",
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
            ) as mock_daily,
            patch(
                "hot_sector_screener.universe_builder.load_daily_history",
                return_value=pd.DataFrame(),
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
            assert (tmp_path / "candidate_outcomes.json").exists()
            assert "quality_report" in result
            assert "outcome_report" in result
            assert result["quality_report"] == {
                "available": False,
                "reason": "future_data_excluded_from_generation",
                "horizons": {},
            }
            assert result["outcome_report"] == result["quality_report"]
            assert result["output_dir"] == str(tmp_path)
            mock_daily.assert_called_once_with("2026-06-19")

    def test_real_mapper_paths_produce_contract_valid_rows(self, monkeypatch, tmp_path):
        _install_contract_path_loaders(monkeypatch)
        topics = [
            {
                "topic": "光通信",
                "weight": 0.8,
                "reasoning": "观测数据主题",
                "related_concepts": ["CPO概念"],
                "source_signals": ["dc_concept"],
            },
            {
                "topic": "AI算力",
                "weight": 0.7,
                "reasoning": "观测数据主题",
                "related_concepts": ["AI算力"],
                "source_signals": ["dc_concept"],
            },
        ]
        screener = Screener(
            {
                "llm": {"enabled": False},
                "output": {"export_signals": False},
                "universe": {"daily_confirmation_enabled": False},
            }
        )

        result = screener.build_universe(
            trade_date="2026-06-19",
            output_dir=str(tmp_path),
            topics=topics,
        )

        validate_candidate_result(result)
        rows = {row["ts_code"]: row for row in result["candidate_universe"]}
        assert set(rows) == {"300308.SZ", "000977.SZ", "600990.SH"}
        assert rows["300308.SZ"]["name"] == "中际旭创"
        assert rows["000977.SZ"]["name"] == "浪潮信息"
        assert rows["600990.SH"]["name"] == "四创电子"
        assert all(row["source_topics"] for row in rows.values())
        assert all(isinstance(row["source_concepts"], list) for row in rows.values())
        assert all(isinstance(row["source_event_tags"], list) for row in rows.values())
        assert rows["600990.SH"]["source_event_tags"] == ["涨停"]

    def test_versioned_holdings_snapshot_writes_separate_overlay(
        self,
        monkeypatch,
        tmp_path,
    ):
        _install_contract_path_loaders(monkeypatch)
        daily = pd.DataFrame(
            [
                {
                    "ts_code": "000002.SZ",
                    "name": "万科A",
                    "trade_date": "20260619",
                    "close": 8.0,
                    "high": 8.2,
                    "low": 7.9,
                    "pct_chg": 0.5,
                    "amount": 200.0,
                }
            ]
        )
        history = pd.DataFrame(
            [
                {
                    "ts_code": "000002.SZ",
                    "trade_date": trade_date,
                    "close": 8.0 + index * 0.1,
                    "high": 8.2 + index * 0.1,
                    "low": 7.9 + index * 0.1,
                    "pct_chg": 0.5,
                    "amount": 180.0 + index * 10,
                }
                for index, trade_date in enumerate(["20260617", "20260618", "20260619"])
            ]
        )
        history_loader = Mock(return_value=history)
        monkeypatch.setattr(universe_builder, "load_daily_data", lambda *_: daily.copy())
        monkeypatch.setattr(universe_builder, "load_daily_history", history_loader)
        topics = [
            {
                "topic": "AI算力",
                "weight": 0.7,
                "reasoning": "观测数据主题",
                "related_concepts": ["AI算力"],
                "source_signals": ["dc_concept"],
            }
        ]
        snapshot = {
            "schema_version": "1.0.0",
            "artifact_type": "hot_sector_holdings_snapshot",
            "market": "CN",
            "as_of_date": "20260618",
            "symbols": ["000002.SZ"],
        }
        screener = Screener(
            {
                "llm": {"enabled": False},
                "output": {"export_signals": False},
                "universe": {"daily_confirmation_enabled": False},
            }
        )

        result = screener.build_universe(
            trade_date="2026-06-19",
            output_dir=str(tmp_path),
            topics=topics,
            holdings_snapshot=snapshot,
        )

        history_loader.assert_called_once_with("20260619", lookback=20)
        candidate_payload = json.loads(
            (tmp_path / "candidate_universe.json").read_text(encoding="utf-8")
        )
        assert candidate_payload["schema_version"] == "2.0.0"
        assert "holdings_overlay" not in candidate_payload
        assert "holdings_overlay_artifact" not in candidate_payload
        overlay_path = tmp_path / "holdings_eligibility_overlay.json"
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        validate_holdings_overlay(overlay)
        assert overlay["rows"][0]["ts_code"] == "000002.SZ"
        assert overlay["rows"][0]["theme_score"] == 0.0
        assert overlay["rows"][0]["hold_eligible"] is True
        assert result["holdings_overlay_artifact"] == str(overlay_path)
        lineage = json.loads((tmp_path / "lineage.json").read_text(encoding="utf-8"))
        assert lineage["output_files"]["holdings_overlay"] == str(overlay_path)

    def test_remote_audit_metadata_is_written_only_to_internal_lineage(
        self,
        monkeypatch,
        tmp_path,
    ):
        _install_contract_path_loaders(monkeypatch)
        provider = Mock()
        provider.complete.return_value = ProviderResponse(
            content=(
                '[{"topic":"AI算力","weight":0.8,"reasoning":"观测日热点",'
                '"related_concepts":["AI算力"],"source_signals":["dc_concept"]}]'
            ),
            receipt=ProviderReceipt(
                provider_id="private-gateway",
                model="topic-classifier-v1",
                api_host="classification.private.test",
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
            ),
        )
        screener = Screener(
            {
                "output": {"export_signals": True},
                "universe": {"daily_confirmation_enabled": False},
            },
            topic_provider=provider,
        )

        screener.build_universe(
            trade_date="2026-06-19",
            output_dir=str(tmp_path),
        )

        lineage = json.loads((tmp_path / "lineage.json").read_text(encoding="utf-8"))
        assert lineage["topic_classification"] == {
            "mode": "remote",
            "provider_receipt": {
                "protocol": "chat_completions.v1",
                "provider_id": "private-gateway",
                "model": "topic-classifier-v1",
                "api_host": "classification.private.test",
                "prompt_sha256": "a" * 64,
                "response_sha256": "b" * 64,
            },
        }
        public_payloads = {
            "candidate_json": (tmp_path / "candidate_universe.json").read_text(encoding="utf-8"),
            "candidate_csv": (tmp_path / "candidate_universe.csv").read_text(encoding="utf-8"),
            "signals_csv": (tmp_path / "signals.csv").read_text(encoding="utf-8"),
            "signals_meta": (tmp_path / "signals.meta.json").read_text(encoding="utf-8"),
            "signals_parquet": pd.read_parquet(tmp_path / "signals.parquet").to_csv(index=False),
        }
        for public_payload in public_payloads.values():
            assert "private-gateway" not in public_payload
            assert "topic-classifier-v1" not in public_payload
            assert "classification.private.test" not in public_payload
            assert "provider_receipt" not in public_payload

    def test_external_topics_use_strict_public_validator(self, monkeypatch):
        for loader_name in (
            "load_ths_hot",
            "load_dc_concept",
            "load_dc_concept_cons",
            "load_kpl_concept_cons",
            "load_kpl_list",
            "load_limit_step",
            "load_limit_cpt_list",
            "load_limit_list_ths",
            "load_hotspot_features",
            "load_industry_signal",
            "load_daily_data",
            "load_daily_history",
        ):
            monkeypatch.setattr(
                universe_builder, loader_name, lambda *args, **kwargs: pd.DataFrame()
            )

        malicious_topics = [
            {
                "topic": "AI精选",
                "weight": 1.0,
                "reasoning": "直接选股",
                "related_concepts": ["300308.SZ"],
                "source_signals": ["model_pick"],
            }
        ]

        with pytest.raises(TopicValidationError):
            Screener().build_universe(
                trade_date="2026-06-19",
                topics=malicious_topics,
            )


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
                "hot_sector_screener.universe_builder.load_kpl_list",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_step",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_cpt_list",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_limit_list_ths",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_hotspot_features",
                return_value=pd.DataFrame(),
            ),
            patch(
                "hot_sector_screener.universe_builder.load_industry_signal",
                return_value=pd.DataFrame(),
            ) as mock_ind,
            patch(
                "hot_sector_screener.universe_builder.load_daily_history",
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
            assert "daily_history" in result
            assert "industry_signal" in result
            assert result["ths_hot"]["rows"] == 0
            mock_ind.assert_called_once_with(as_of_date="20260619", run_dir=None)


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
            ) as mock_ind,
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
            mock_ind.assert_called_once_with(as_of_date="20260619", run_dir=None)
