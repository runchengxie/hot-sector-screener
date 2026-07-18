from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .candidate_contract import CANDIDATE_FEATURE_SET_ID, CANDIDATE_MODEL_ID

_LLM_ALLOWED_FIELDS = frozenset({"enabled", "adapter", "prompt_template"})
_LLM_ADAPTER = "chat_completions"


def normalize_llm_config(value: object) -> dict[str, Any]:
    """Validate supplier-neutral topic-classification settings."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("llm config must be a mapping")
    unknown = sorted(str(field) for field in set(value) - _LLM_ALLOWED_FIELDS)
    if unknown:
        raise ValueError(
            "unsupported llm config fields: "
            + ", ".join(unknown)
            + "; runtime provider settings must use explicit LLM_* environment values"
        )

    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("llm.enabled must be a boolean")
    adapter = value.get("adapter", _LLM_ADAPTER)
    if adapter != _LLM_ADAPTER:
        raise ValueError(f"llm.adapter must be {_LLM_ADAPTER}")
    prompt_template = value.get("prompt_template", "default")
    if prompt_template != "default":
        raise ValueError("llm.prompt_template must be default")
    return {
        "enabled": enabled,
        "adapter": adapter,
        "prompt_template": prompt_template,
    }


def load_config(config_path: str | Path) -> dict[str, Any]:
    """Load a hot-sector-screener experiment config YAML."""
    resolved = Path(config_path).expanduser()
    if not resolved.is_absolute():
        resolved = (Path.cwd() / resolved).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Config file not found: {resolved}")

    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError(f"Config root must be a mapping, got {type(payload).__name__}")

    return {
        "config_path": str(resolved),
        "market": payload.get("market", "a_share"),
        "date": str(payload.get("date", "")),
        "hotspot_sources": payload.get(
            "hotspot_sources",
            [
                "dc_concept",
                "dc_concept_cons",
                "kpl_concept_cons",
                "kpl_list",
                "limit_step",
                "limit_cpt_list",
                "limit_list_ths",
            ],
        ),
        "llm": normalize_llm_config(payload.get("llm")),
        "universe": {
            "max_candidates": payload.get("universe", {}).get("max_candidates", 100),
            "min_candidates": payload.get("universe", {}).get("min_candidates", 30),
            "min_daily_amount_rank_pct": payload.get("universe", {}).get(
                "min_daily_amount_rank_pct", 60
            ),
            "max_price": payload.get("universe", {}).get("max_price", 200.0),
            "min_price": payload.get("universe", {}).get("min_price", 2.0),
            "max_st_allow": payload.get("universe", {}).get("max_st_allow", False),
            "topics_per_run": payload.get("universe", {}).get("topics_per_run", 5),
            "stocks_per_topic": payload.get("universe", {}).get("stocks_per_topic", 25),
            "hotspot_feature_overlay": payload.get("universe", {}).get(
                "hotspot_feature_overlay", True
            ),
            "hotspot_feature_weight": payload.get("universe", {}).get(
                "hotspot_feature_weight", 0.25
            ),
            "daily_confirmation_enabled": payload.get("universe", {}).get(
                "daily_confirmation_enabled", True
            ),
            "daily_confirmation_weight": payload.get("universe", {}).get(
                "daily_confirmation_weight", 0.20
            ),
            "daily_confirmation_lookback": payload.get("universe", {}).get(
                "daily_confirmation_lookback", 20
            ),
            "min_daily_confirmation_score": payload.get("universe", {}).get(
                "min_daily_confirmation_score"
            ),
            "confidence_enabled": payload.get("universe", {}).get("confidence_enabled", True),
        },
        "output": {
            "format": payload.get("output", {}).get("format", "csv"),
            "publish": payload.get("output", {}).get("publish", False),
            "export_signals": payload.get("output", {}).get("export_signals", True),
            "signal_model_version": payload.get("output", {}).get(
                "signal_model_version", CANDIDATE_MODEL_ID
            ),
            "signal_feature_set_id": payload.get("output", {}).get(
                "signal_feature_set_id", CANDIDATE_FEATURE_SET_ID
            ),
            "eligible_for_live": False,
        },
        "rotation_signal_dir": payload.get("rotation_signal_dir"),
    }


def default_config() -> dict[str, Any]:
    """Return defaults for building a next-session pool from completed EOD data."""
    return {
        "market": "a_share",
        "date": "",
        "hotspot_sources": [
            "dc_concept",
            "dc_concept_cons",
            "kpl_concept_cons",
            "kpl_list",
            "limit_step",
            "limit_cpt_list",
            "limit_list_ths",
        ],
        "llm": normalize_llm_config(None),
        "universe": {
            "max_candidates": 100,
            "min_candidates": 30,
            "min_daily_amount_rank_pct": 60,
            "max_price": 200.0,
            "min_price": 2.0,
            "max_st_allow": False,
            "topics_per_run": 5,
            "stocks_per_topic": 25,
            "hotspot_feature_overlay": True,
            "hotspot_feature_weight": 0.25,
            "daily_confirmation_enabled": True,
            "daily_confirmation_weight": 0.20,
            "daily_confirmation_lookback": 20,
            "min_daily_confirmation_score": None,
            "confidence_enabled": True,
        },
        "output": {
            "format": "csv",
            "publish": False,
            "export_signals": True,
            "signal_model_version": CANDIDATE_MODEL_ID,
            "signal_feature_set_id": CANDIDATE_FEATURE_SET_ID,
            "eligible_for_live": False,
        },
        "rotation_signal_dir": None,
    }
