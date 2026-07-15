from __future__ import annotations

import hashlib
import json
import math
import os
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .topic_text_safety import TopicTextSafetyError, validate_no_secret_echo

_MAX_PROMPT_BYTES = 2 * 1024 * 1024
_MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
_REQUIRED_ENVIRONMENT = (
    "LLM_API_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_PROVIDER_ID",
)


class TopicProviderError(RuntimeError):
    """A sanitized provider configuration, transport, or response failure."""


@dataclass(frozen=True, slots=True)
class ProviderReceipt:
    """Internal-only lineage for one accepted provider response."""

    provider_id: str
    model: str
    api_host: str
    prompt_sha256: str
    response_sha256: str
    protocol: str = "chat_completions.v1"

    def to_lineage(self) -> dict[str, str]:
        return {
            "protocol": self.protocol,
            "provider_id": self.provider_id,
            "model": self.model,
            "api_host": self.api_host,
            "prompt_sha256": self.prompt_sha256,
            "response_sha256": self.response_sha256,
        }


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    content: str
    receipt: ProviderReceipt


class TopicProvider(Protocol):
    def complete(self, prompt: str) -> ProviderResponse:
        """Return one topic-classification response with internal lineage."""
        ...


def _url_origin(url: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlparse(url)
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port or default_port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that could forward authorization to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if _url_origin(req.full_url) != _url_origin(target):
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "provider redirect to a different origin was blocked",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, target)


def _required_environment(environ: Mapping[str, str]) -> dict[str, str]:
    values = {name: str(environ.get(name, "")) for name in _REQUIRED_ENVIRONMENT}
    missing = [name for name, value in values.items() if not value.strip()]
    if missing:
        raise TopicProviderError(
            "remote topic provider requires explicit environment values: " + ", ".join(missing)
        )
    return values


def _validate_identifier(name: str, value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TopicProviderError(f"{name} must be a non-empty value without surrounding whitespace")
    if len(value) > 200 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise TopicProviderError(f"{name} contains unsupported characters")
    return value


def _validate_api_key(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TopicProviderError(
            "LLM_API_KEY must be a non-empty value without surrounding whitespace"
        )
    if len(value) > 8192 or any(not 33 <= ord(character) <= 126 for character in value):
        raise TopicProviderError("LLM_API_KEY contains unsupported characters")
    return value


def _validated_base_url(value: str) -> tuple[str, str]:
    try:
        if (
            not isinstance(value, str)
            or not value
            or any(
                character.isspace()
                or ord(character) > 127
                or unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp", "Zs"}
                for character in value
            )
        ):
            raise TopicProviderError("LLM_API_URL contains unsupported characters")
        parsed = urllib.parse.urlparse(value)
        hostname = parsed.hostname
        _ = parsed.port
        if (
            parsed.scheme.casefold() != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise TopicProviderError(
                "LLM_API_URL must be an HTTPS URL without embedded credentials, query, or fragment"
            )
        return value.rstrip("/"), hostname.casefold().rstrip(".")
    except TopicProviderError:
        raise
    except Exception as exc:
        raise TopicProviderError("LLM_API_URL is invalid") from exc


def _decode_content(body: bytes) -> str:
    if len(body) > _MAX_PROVIDER_RESPONSE_BYTES:
        raise TopicProviderError("provider response exceeded the size limit")
    try:
        result = json.loads(body.decode("utf-8"))
        choices = result["choices"]
        message = choices[0]["message"]
        content = message["content"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise TopicProviderError("provider returned an invalid response schema") from exc
    if not isinstance(content, str) or not content.strip():
        raise TopicProviderError("provider returned an empty response")
    return content


@dataclass(frozen=True, slots=True)
class ChatCompletionsAdapter:
    """Supplier-neutral client for the chat-completions JSON wire protocol."""

    api_url: str
    model: str
    provider_id: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 120.0
    api_host: str = field(init=False)

    def __post_init__(self) -> None:
        api_url, api_host = _validated_base_url(self.api_url)
        object.__setattr__(self, "api_url", api_url)
        object.__setattr__(self, "api_host", api_host)
        object.__setattr__(self, "model", _validate_identifier("LLM_MODEL", self.model))
        object.__setattr__(
            self,
            "provider_id",
            _validate_identifier("LLM_PROVIDER_ID", self.provider_id),
        )
        object.__setattr__(self, "api_key", _validate_api_key(self.api_key))
        try:
            timeout_is_valid = math.isfinite(self.timeout_seconds) and self.timeout_seconds > 0
        except (TypeError, ValueError):
            timeout_is_valid = False
        if not timeout_is_valid:
            raise TopicProviderError("provider timeout must be a positive finite number")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float = 120.0,
    ) -> ChatCompletionsAdapter:
        values = _required_environment(os.environ if environ is None else environ)
        return cls(
            api_url=values["LLM_API_URL"],
            api_key=values["LLM_API_KEY"],
            model=values["LLM_MODEL"],
            provider_id=values["LLM_PROVIDER_ID"],
            timeout_seconds=timeout_seconds,
        )

    def complete(self, prompt: str) -> ProviderResponse:
        try:
            prompt_bytes = prompt.encode("utf-8")
            if len(prompt_bytes) > _MAX_PROMPT_BYTES:
                raise TopicProviderError("topic prompt exceeded the size limit")

            request_semantics = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是一个 A 股市场主题识别助手。只做信息压缩和分类，"
                            "不做投资建议。输出严格的 JSON 格式。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
            }
            payload = json.dumps(
                request_semantics,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{self.api_url}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            opener = urllib.request.build_opener(_SameOriginRedirectHandler())
            with opener.open(request, timeout=self.timeout_seconds) as response:
                body = response.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
            content = _decode_content(body)
            validate_no_secret_echo(content, (self.api_key,))
        except TopicProviderError:
            raise
        except TopicTextSafetyError as exc:
            raise TopicProviderError("provider response failed safety validation") from exc
        except Exception as exc:  # normalize Request, redirects, HTTP parsing, decoding, and reads
            raise TopicProviderError("provider request failed") from exc

        return ProviderResponse(
            content=content,
            receipt=ProviderReceipt(
                provider_id=self.provider_id,
                model=self.model,
                api_host=self.api_host,
                prompt_sha256=hashlib.sha256(payload).hexdigest(),
                response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            ),
        )


__all__ = [
    "ChatCompletionsAdapter",
    "ProviderReceipt",
    "ProviderResponse",
    "TopicProvider",
    "TopicProviderError",
]
