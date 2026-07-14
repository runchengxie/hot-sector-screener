"""Regression tests for credentials used with DeepSeek endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from hot_sector_screener import topic_classifier


def test_topic_classifier_rejects_openai_key_before_network(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-only-key")
    build_opener = Mock(side_effect=AssertionError("network request must not run"))
    monkeypatch.setattr(topic_classifier.urllib.request, "build_opener", build_opener)

    with pytest.raises(RuntimeError, match="LLM_API_KEY or DEEPSEEK_API_KEY"):
        topic_classifier._call_llm_for_topics("test prompt")

    build_opener.assert_not_called()


@pytest.mark.parametrize("key_name", ["LLM_API_KEY", "DEEPSEEK_API_KEY"])
def test_topic_classifier_accepts_supported_keys(monkeypatch, key_name):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(key_name, "supported-key")
    response = Mock()
    response.read.return_value = b'{"choices":[{"message":{"content":"classified topics"}}]}'
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = response
    monkeypatch.setattr(
        topic_classifier.urllib.request,
        "build_opener",
        Mock(return_value=opener),
    )

    assert topic_classifier._call_llm_for_topics("test prompt") == "classified topics"

    request = opener.open.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer supported-key"


def test_custom_endpoint_requires_independent_generic_key(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-leave-deepseek")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    build_opener = Mock(side_effect=AssertionError("network request must not run"))
    monkeypatch.setattr(topic_classifier.urllib.request, "build_opener", build_opener)

    with pytest.raises(RuntimeError, match="independent LLM_API_KEY"):
        topic_classifier._call_llm_for_topics("test prompt")

    build_opener.assert_not_called()


def test_custom_endpoint_uses_generic_key(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    response = Mock()
    response.read.return_value = b'{"choices":[{"message":{"content":"topics"}}]}'
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = response
    monkeypatch.setattr(
        topic_classifier.urllib.request,
        "build_opener",
        Mock(return_value=opener),
    )

    assert topic_classifier._call_llm_for_topics("test prompt") == "topics"

    request = opener.open.call_args.args[0]
    assert request.full_url == "https://llm.example.test/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer generic-key"


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"{}",
        b'{"choices":[]}',
        b'{"choices":[{"message":{"content":null}}]}',
    ],
)
def test_malformed_success_response_fails_closed(monkeypatch, body):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "supported-key")
    response = Mock()
    response.read.return_value = body
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = response
    monkeypatch.setattr(
        topic_classifier.urllib.request,
        "build_opener",
        Mock(return_value=opener),
    )

    with pytest.raises(RuntimeError, match=r"invalid response schema|empty response"):
        topic_classifier._call_llm_for_topics("test prompt")


def test_oversized_success_response_fails_closed(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "supported-key")
    response = Mock()
    response.read.return_value = b"x" * (topic_classifier._MAX_LLM_RESPONSE_BYTES + 1)
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = response
    monkeypatch.setattr(
        topic_classifier.urllib.request,
        "build_opener",
        Mock(return_value=opener),
    )

    with pytest.raises(RuntimeError, match="size limit"):
        topic_classifier._call_llm_for_topics("test prompt")


@pytest.mark.parametrize(
    "api_url",
    [
        "https://api.deepseek.com:invalid/v1",
        "https://api.deepseek.com/v1?redirect=https://attacker.example",
        "https://api.deepseek.com/v1#fragment",
    ],
)
def test_ambiguous_endpoint_is_rejected_before_network(monkeypatch, api_url):
    monkeypatch.setenv("LLM_API_URL", api_url)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    build_opener = Mock(side_effect=AssertionError("network request must not run"))
    monkeypatch.setattr(topic_classifier.urllib.request, "build_opener", build_opener)

    with pytest.raises(RuntimeError):
        topic_classifier._call_llm_for_topics("test prompt")

    build_opener.assert_not_called()


@pytest.mark.parametrize(
    ("api_url", "key_name", "key_value"),
    [
        (None, "DEEPSEEK_API_KEY", "deepseek-secret"),
        ("https://llm.example.test/v1", "LLM_API_KEY", "generic-secret"),
    ],
)
def test_cross_origin_redirect_is_blocked_for_every_key_type(
    monkeypatch,
    api_url,
    key_name,
    key_value,
):
    monkeypatch.delenv("LLM_API_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    if api_url:
        monkeypatch.setenv("LLM_API_URL", api_url)
    monkeypatch.setenv(key_name, key_value)
    captured: dict[str, object] = {}

    class RedirectingOpener:
        def __init__(self, handler):
            self.handler = handler

        def open(self, request, timeout):
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return self.handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {"Location": "https://attacker.example/collect"},
                "https://attacker.example/collect",
            )

    def build_opener(handler):
        assert isinstance(handler, topic_classifier._SameOriginRedirectHandler)
        return RedirectingOpener(handler)

    monkeypatch.setattr(topic_classifier.urllib.request, "build_opener", build_opener)

    with pytest.raises(RuntimeError, match="different origin"):
        topic_classifier._call_llm_for_topics("test prompt")

    assert captured == {"authorization": f"Bearer {key_value}", "timeout": 120}


@pytest.mark.parametrize(
    "api_url",
    [
        "http://api.deepseek.com/v1",
        "https://api.deepseek.com.evil.example/v1",
    ],
)
def test_deepseek_key_is_not_sent_to_lookalike_or_insecure_hosts(monkeypatch, api_url):
    monkeypatch.setenv("LLM_API_URL", api_url)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    build_opener = Mock(side_effect=AssertionError("network request must not run"))
    monkeypatch.setattr(topic_classifier.urllib.request, "build_opener", build_opener)

    with pytest.raises(RuntimeError):
        topic_classifier._call_llm_for_topics("test prompt")

    build_opener.assert_not_called()
