"""Tests for the universe builder."""
from __future__ import annotations

from hotspot_universe.config import default_config


class TestConfig:
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
