"""Regression tests for credentials used with DeepSeek endpoints."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from hot_sector_screener import topic_classifier


def _load_deepseek_pick():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "deepseek_pick.py"
    spec = importlib.util.spec_from_file_location("hotsector_deepseek_pick", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stock_picker_rejects_openai_key_before_network(monkeypatch):
    deepseek_pick = _load_deepseek_pick()
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-only-key")
    urlopen = Mock(side_effect=AssertionError("network request must not run"))
    monkeypatch.setattr(deepseek_pick.urllib.request, "urlopen", urlopen)

    with pytest.raises(RuntimeError, match="Set DEEPSEEK_API_KEY"):
        deepseek_pick.call_deepseek("test prompt")

    urlopen.assert_not_called()


def test_topic_classifier_rejects_openai_key_before_network(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-only-key")
    urlopen = Mock(side_effect=AssertionError("network request must not run"))
    monkeypatch.setattr(topic_classifier.urllib.request, "urlopen", urlopen)

    with pytest.raises(RuntimeError, match="LLM_API_KEY or DEEPSEEK_API_KEY"):
        topic_classifier._call_llm_for_topics("test prompt")

    urlopen.assert_not_called()


@pytest.mark.parametrize("key_name", ["LLM_API_KEY", "DEEPSEEK_API_KEY"])
def test_topic_classifier_accepts_supported_keys(monkeypatch, key_name):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(key_name, "supported-key")
    response = Mock()
    response.read.return_value = b'{"choices":[{"message":{"content":"classified topics"}}]}'
    urlopen = MagicMock()
    urlopen.return_value.__enter__.return_value = response
    monkeypatch.setattr(topic_classifier.urllib.request, "urlopen", urlopen)

    assert topic_classifier._call_llm_for_topics("test prompt") == "classified topics"

    request = urlopen.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer supported-key"
