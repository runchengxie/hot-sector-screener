"""Tests for config loading."""
from __future__ import annotations

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
        assert len(cfg["hotspot_sources"]) == 3
        assert cfg["market"] == "a_share"
        assert cfg["rotation_signal_dir"] is None

    def test_default_llm_config(self):
        cfg = default_config()
        llm = cfg["llm"]
        assert llm["enabled"] is True
        assert llm["model"] == "deepseek-reasoner"
        assert llm["provider"] == "deepseek"

    def test_default_universe_config(self):
        cfg = default_config()
        uni = cfg["universe"]
        assert uni["min_daily_amount_rank_pct"] == 80
        assert uni["max_price"] == 200.0
        assert uni["min_price"] == 2.0
        assert uni["max_st_allow"] is False
        assert uni["topics_per_run"] == 5
        assert uni["stocks_per_topic"] == 25


class TestLoadConfig:
    def test_load_default_yaml(self, tmp_path):
        yaml_content = """
market: a_share
hotspot_sources:
  - ths_hot
universe:
  max_candidates: 50
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

    def test_load_empty_yaml(self, tmp_path):
        config_path = tmp_path / "empty.yml"
        config_path.write_text("")
        cfg = load_config(str(config_path))
        assert cfg["market"] == "a_share"
        assert cfg["llm"]["enabled"] is True

    def test_load_nonexistent_raises(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yml")
