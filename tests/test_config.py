"""Tests for config loading."""

from __future__ import annotations

import pytest

from hot_sector_screener.config import default_config, load_config


class TestDefaultConfig:
    def test_default_config_has_all_keys(self):
        cfg = default_config()
        assert "market" in cfg
        assert "hotspot_sources" in cfg
        assert "llm" in cfg
        assert "universe" in cfg
        assert "output" in cfg
        assert cfg["universe"]["max_candidates"] == 100
        assert cfg["universe"]["min_candidates"] == 30
        assert len(cfg["hotspot_sources"]) == 7
        assert cfg["market"] == "a_share"
        assert cfg["rotation_signal_dir"] is None

    def test_default_llm_config_is_supplier_neutral(self):
        cfg = default_config()
        llm = cfg["llm"]
        assert llm["enabled"] is True
        assert llm["adapter"] == "chat_completions"
        assert llm["prompt_template"] == "default"
        assert set(llm) == {"enabled", "adapter", "prompt_template"}

    def test_default_universe_config(self):
        cfg = default_config()
        uni = cfg["universe"]
        assert uni["min_daily_amount_rank_pct"] == 60
        assert uni["max_price"] == 200.0
        assert uni["min_price"] == 2.0
        assert uni["max_st_allow"] is False
        assert uni["topics_per_run"] == 5
        assert uni["stocks_per_topic"] == 25
        assert uni["hotspot_feature_overlay"] is True
        assert uni["hotspot_feature_weight"] == 0.25
        assert cfg["output"]["export_signals"] is True
        assert cfg["output"]["eligible_for_live"] is False


class TestLoadConfig:
    def test_load_default_yaml(self, tmp_path):
        yaml_content = """
market: a_share
hotspot_sources:
  - ths_hot
universe:
  max_candidates: 50
output:
  eligible_for_live: true
"""
        config_path = tmp_path / "test_config.yml"
        config_path.write_text(yaml_content)
        cfg = load_config(str(config_path))

        assert cfg["market"] == "a_share"
        assert cfg["hotspot_sources"] == ["ths_hot"]
        assert cfg["universe"]["max_candidates"] == 50
        # defaults preserved for unspecified keys
        assert cfg["universe"]["min_candidates"] == 30
        assert cfg["universe"]["min_price"] == 2.0
        assert cfg["universe"]["hotspot_feature_overlay"] is True
        assert cfg["output"]["export_signals"] is True
        assert cfg["output"]["eligible_for_live"] is False

    def test_load_empty_yaml(self, tmp_path):
        config_path = tmp_path / "empty.yml"
        config_path.write_text("")
        cfg = load_config(str(config_path))
        assert cfg["market"] == "a_share"
        assert cfg["llm"]["enabled"] is True

    def test_load_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yml")

    @pytest.mark.parametrize("legacy_field", ["model", "provider"])
    def test_legacy_runtime_fields_are_explicitly_rejected(self, tmp_path, legacy_field):
        config_path = tmp_path / "legacy.yml"
        config_path.write_text(
            f"llm:\n  enabled: true\n  {legacy_field}: deployment-specific\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match=legacy_field):
            load_config(config_path)

    def test_unknown_adapter_is_rejected(self, tmp_path):
        config_path = tmp_path / "invalid.yml"
        config_path.write_text(
            "llm:\n  adapter: deployment-specific\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match=r"llm\.adapter"):
            load_config(config_path)
