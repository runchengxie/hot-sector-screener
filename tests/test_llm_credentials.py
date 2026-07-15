"""Regression tests for the supplier-neutral topic provider adapter."""

from __future__ import annotations

import hashlib
import http.client
import json
import urllib.request
from typing import Any, cast
from unittest.mock import MagicMock, Mock

import pytest

from hot_sector_screener import topic_provider
from hot_sector_screener.topic_provider import (
    ChatCompletionsAdapter,
    TopicProviderError,
)

_ENV = {
    "LLM_API_URL": "https://model-gateway.example.test/v1",
    "LLM_API_KEY": "generic-secret",
    "LLM_MODEL": "topic-classifier-v1",
    "LLM_PROVIDER_ID": "primary-gateway",
}


def _adapter(**overrides: object) -> ChatCompletionsAdapter:
    values: dict[str, Any] = {
        "api_url": _ENV["LLM_API_URL"],
        "api_key": _ENV["LLM_API_KEY"],
        "model": _ENV["LLM_MODEL"],
        "provider_id": _ENV["LLM_PROVIDER_ID"],
    }
    values.update(overrides)
    return ChatCompletionsAdapter(**values)


def _mock_response(monkeypatch: pytest.MonkeyPatch, body: bytes) -> MagicMock:
    response = Mock()
    response.read.return_value = body
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = response
    monkeypatch.setattr(
        topic_provider.urllib.request,
        "build_opener",
        Mock(return_value=opener),
    )
    return opener


@pytest.mark.parametrize("missing", tuple(_ENV))
def test_remote_adapter_requires_every_explicit_environment_value(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    environ = dict(_ENV)
    environ.pop(missing)
    build_opener = Mock(side_effect=AssertionError("network request must not run"))
    monkeypatch.setattr(topic_provider.urllib.request, "build_opener", build_opener)

    with pytest.raises(TopicProviderError, match=missing):
        ChatCompletionsAdapter.from_environment(environ)

    build_opener.assert_not_called()


def test_adapter_uses_explicit_configuration_and_hashes_full_request_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = "classified topics"
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    opener = _mock_response(monkeypatch, body)

    adapter = ChatCompletionsAdapter.from_environment(_ENV, timeout_seconds=7.0)
    response = adapter.complete("test prompt")

    request = opener.open.call_args.args[0]
    assert isinstance(request.data, bytes)
    payload = json.loads(request.data)
    assert request.full_url == "https://model-gateway.example.test/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer generic-secret"
    assert opener.open.call_args.kwargs == {"timeout": 7.0}
    assert payload["model"] == "topic-classifier-v1"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][-1] == {"role": "user", "content": "test prompt"}
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 2000
    assert response.content == content
    assert response.receipt.to_lineage() == {
        "protocol": "chat_completions.v1",
        "provider_id": "primary-gateway",
        "model": "topic-classifier-v1",
        "api_host": "model-gateway.example.test",
        "prompt_sha256": hashlib.sha256(request.data).hexdigest(),
        "response_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }
    assert "generic-secret" not in repr(adapter)
    assert "generic-secret" not in json.dumps(response.receipt.to_lineage())


@pytest.mark.parametrize(
    "api_url",
    [
        "http://model-gateway.example.test/v1",
        "https://user:secret@model-gateway.example.test/v1",
        "https://model-gateway.example.test:invalid/v1",
        "https://model-gateway.example.test/v1?target=other",
        "https://model-gateway.example.test/v1#fragment",
        " https://model-gateway.example.test/v1",
        "https://model-gateway.example.test/v1\n",
        "https://model gateway.example.test/v1",
        "https://model-gateway.example.test/\u200bv1",
        "https://[::1/v1",
    ],
)
def test_ambiguous_or_insecure_endpoint_is_rejected_before_network(
    monkeypatch: pytest.MonkeyPatch,
    api_url: str,
) -> None:
    build_opener = Mock(side_effect=AssertionError("network request must not run"))
    monkeypatch.setattr(topic_provider.urllib.request, "build_opener", build_opener)

    with pytest.raises(TopicProviderError):
        _adapter(api_url=api_url)

    build_opener.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"{}",
        b'{"choices":[]}',
        b'{"choices":[{"message":{"content":null}}]}',
    ],
)
def test_malformed_success_response_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    _mock_response(monkeypatch, body)

    with pytest.raises(TopicProviderError, match=r"invalid response schema|empty response"):
        _adapter().complete("test prompt")


def test_oversized_success_response_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_response(
        monkeypatch,
        b"x" * (topic_provider._MAX_PROVIDER_RESPONSE_BYTES + 1),
    )

    with pytest.raises(TopicProviderError, match="size limit"):
        _adapter().complete("test prompt")


def test_oversized_prompt_fails_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    build_opener = Mock(side_effect=AssertionError("network request must not run"))
    monkeypatch.setattr(topic_provider.urllib.request, "build_opener", build_opener)

    with pytest.raises(TopicProviderError, match="prompt exceeded"):
        _adapter().complete("x" * (topic_provider._MAX_PROMPT_BYTES + 1))

    build_opener.assert_not_called()


def test_request_constructor_failure_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        topic_provider.urllib.request,
        "Request",
        Mock(side_effect=ValueError("request implementation detail")),
    )

    with pytest.raises(TopicProviderError, match="provider request failed") as exc_info:
        _adapter().complete("test prompt")

    assert "implementation detail" not in str(exc_info.value)


@pytest.mark.parametrize(
    "failure",
    [
        http.client.HTTPException("invalid HTTP state"),
        http.client.IncompleteRead(b"partial", 10),
        ValueError("invalid response read"),
    ],
)
def test_http_and_read_failures_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    response = Mock()
    response.read.side_effect = failure
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = response
    monkeypatch.setattr(
        topic_provider.urllib.request,
        "build_opener",
        Mock(return_value=opener),
    )

    with pytest.raises(TopicProviderError, match="provider request failed") as exc_info:
        _adapter().complete("test prompt")

    assert str(failure) not in str(exc_info.value)


def test_configured_api_key_echo_is_rejected_even_when_separated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(
        {"choices": [{"message": {"content": "generic＿se cret"}}]},
        ensure_ascii=False,
    ).encode()
    _mock_response(monkeypatch, body)

    with pytest.raises(TopicProviderError, match="provider response failed safety validation"):
        _adapter().complete("test prompt")


def test_cross_origin_redirect_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
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
                {"Location": "https://other-origin.example.test/collect"},
                "https://other-origin.example.test/collect",
            )

    def build_opener(handler):
        assert isinstance(handler, topic_provider._SameOriginRedirectHandler)
        return RedirectingOpener(handler)

    monkeypatch.setattr(topic_provider.urllib.request, "build_opener", build_opener)

    with pytest.raises(TopicProviderError, match="provider request failed"):
        _adapter().complete("test prompt")

    assert captured == {"authorization": "Bearer generic-secret", "timeout": 120.0}


def test_same_origin_redirect_is_allowed() -> None:
    handler = topic_provider._SameOriginRedirectHandler()
    request = urllib.request.Request(
        "https://model-gateway.example.test/v1/chat/completions",
        data=b"{}",
        method="POST",
    )

    redirected = cast(Any, handler).redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://model-gateway.example.test/v2/chat/completions",
    )

    assert redirected is not None
    assert redirected.full_url == "https://model-gateway.example.test/v2/chat/completions"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"model": "bad\nmodel"}, "LLM_MODEL"),
        ({"provider_id": "bad\rprovider"}, "LLM_PROVIDER_ID"),
        ({"api_key": "bad\nkey"}, "LLM_API_KEY"),
        ({"timeout_seconds": 0}, "timeout"),
        ({"api_url": object()}, "LLM_API_URL"),
        ({"model": object()}, "LLM_MODEL"),
        ({"api_key": object()}, "LLM_API_KEY"),
        ({"timeout_seconds": "slow"}, "timeout"),
    ],
)
def test_invalid_runtime_values_fail_closed(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(TopicProviderError, match=message):
        _adapter(**overrides)
