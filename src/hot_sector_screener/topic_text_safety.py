from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import NoReturn


class TopicTextSafetyError(ValueError):
    """A generic public-text rejection that never includes matched metadata."""


_PUBLIC_TEXT_ERROR = "provider response failed public text safety validation"
_DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_IPV4_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_URL_PATTERN = re.compile(
    rf"(?i)(?:\b(?:https?|ftp)\s*[:：]|\bwww\s*[.．]|"
    rf"(?<![a-z0-9-])(?:{_DOMAIN_LABEL}\s*[.．]\s*)+[a-z]{{2,63}}"
    rf"(?![a-z0-9-])|(?<![0-9])(?:{_IPV4_OCTET}\s*[.．]\s*){{3}}"
    rf"{_IPV4_OCTET}(?![0-9]))"
)
_SYSTEM_METADATA_PHRASES = (
    "accesskey",
    "accesstoken",
    "apiendpoint",
    "apikey",
    "apiurl",
    "authorizationheader",
    "authorizationtoken",
    "bearertoken",
    "endpointurl",
    "internalmetadata",
    "lineagemetadata",
    "modelid",
    "modelidentifier",
    "modelmetadata",
    "modelname",
    "prompthash",
    "promptsha",
    "promptsha256",
    "providermetadata",
    "providerid",
    "provideridentifier",
    "providername",
    "receiptmetadata",
    "requesthash",
    "requestsha",
    "responsehash",
    "responsesha",
    "responsesha256",
    "runtimemetadata",
    "supplierid",
    "suppliermetadata",
    "systemmetadata",
    "vendorid",
    "vendormetadata",
    "api地址",
    "api密钥",
    "供应商元数据",
    "供应商标识",
    "供应商编号",
    "内部元数据",
    "提示词哈希",
    "接口url",
    "接口地址",
    "接口密钥",
    "模型元数据",
    "模型名称",
    "模型标识",
    "模型编号",
    "端点url",
    "端点地址",
    "系统元数据",
    "访问令牌",
    "访问密钥",
    "认证令牌",
    "认证密钥",
    "请求哈希",
    "运行时元数据",
    "身份令牌",
    "授权令牌",
    "响应哈希",
)
_COMPACT_URL_MARKERS = ("http", "www")
_COMPONENT_METADATA_FIELDS = frozenset({"provider_id", "model"})
_GENERIC_METADATA_COMPONENTS = frozenset(
    {
        "adapter",
        "assistant",
        "backend",
        "classifier",
        "completion",
        "completions",
        "default",
        "deployment",
        "engine",
        "example",
        "family",
        "frontend",
        "foundation",
        "gateway",
        "generic",
        "global",
        "instruct",
        "language",
        "latest",
        "market",
        "metadata",
        "model",
        "models",
        "multimodal",
        "platform",
        "preview",
        "primary",
        "production",
        "provider",
        "regional",
        "reasoner",
        "research",
        "runtime",
        "server",
        "service",
        "standard",
        "system",
        "testing",
        "version",
        "vision",
    }
)
_MIN_METADATA_COMPONENT_LENGTH = 6


def compact_for_safety(value: str) -> str:
    """Normalize compatibility forms and remove separator-based evasions."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _metadata_components(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return tuple(
        component
        for raw_component in re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
        if (component := compact_for_safety(raw_component))
    )


def _metadata_needles(metadata: Mapping[str, str]) -> tuple[set[str], set[str]]:
    substring_needles: set[str] = set()
    exact_needles: set[str] = set()
    for field_name, value in metadata.items():
        compact_value = compact_for_safety(str(value))
        if compact_value:
            if len(compact_value) >= 4:
                substring_needles.add(compact_value)
            else:
                exact_needles.add(compact_value)
        if field_name in _COMPONENT_METADATA_FIELDS:
            substring_needles.update(
                component
                for component in _metadata_components(str(value))
                if len(component) >= _MIN_METADATA_COMPONENT_LENGTH
                and component not in _GENERIC_METADATA_COMPONENTS
            )
    return substring_needles, exact_needles


def _reject() -> NoReturn:
    raise TopicTextSafetyError(_PUBLIC_TEXT_ERROR)


def validate_public_topic_texts(
    topics: Iterable[Mapping[str, object]],
    *,
    provider_metadata: Mapping[str, str],
) -> None:
    """Reject provider/system metadata in remote free-form customer text.

    ``related_concepts`` is deliberately excluded: it is separately bound to the
    observation vocabulary. Only the remote free-form ``topic`` and ``reasoning``
    fields need this last-mile leak guard.
    """
    substring_needles, exact_needles = _metadata_needles(provider_metadata)
    for topic in topics:
        for field_name in ("topic", "reasoning"):
            value = topic.get(field_name)
            if not isinstance(value, str):
                _reject()
            normalized = unicodedata.normalize("NFKC", value)
            compact_value = compact_for_safety(normalized)
            if _URL_PATTERN.search(normalized):
                _reject()
            if any(marker in compact_value for marker in _COMPACT_URL_MARKERS):
                _reject()
            if any(phrase in compact_value for phrase in _SYSTEM_METADATA_PHRASES):
                _reject()
            if compact_value in exact_needles:
                _reject()
            if any(needle in compact_value for needle in substring_needles):
                _reject()


def validate_no_secret_echo(content: str, secrets: Iterable[str]) -> None:
    """Reject a response containing a configured credential, including obfuscation."""
    compact_content = compact_for_safety(content)
    for secret in secrets:
        compact_secret = compact_for_safety(secret)
        if compact_secret and compact_secret in compact_content:
            _reject()


__all__ = [
    "TopicTextSafetyError",
    "compact_for_safety",
    "validate_no_secret_echo",
    "validate_public_topic_texts",
]
