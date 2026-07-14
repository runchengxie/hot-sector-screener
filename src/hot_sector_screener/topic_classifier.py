from __future__ import annotations

import json
import os as _os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .concept_registry import canonicalize_concept

_ALLOWED_SOURCE_SIGNALS = frozenset({"ths_hot", "dc_concept", "etf_rotation", "limit_cpt_list"})
_TOPIC_FIELDS = frozenset({"topic", "weight", "reasoning", "related_concepts", "source_signals"})
_DEEPSEEK_API_URL = "https://api.deepseek.com/v1"
_DEEPSEEK_HOST = "api.deepseek.com"
_MAX_LLM_RESPONSE_BYTES = 2 * 1024 * 1024
_STOCK_CODE_PATTERN = re.compile(r"^\d{6}(?:\.(?:SH|SZ|BJ))?$", re.IGNORECASE)


class TopicValidationError(ValueError):
    pass


def _url_origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlparse(url)
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port or default_port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that could forward Authorization outside the configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if _url_origin(req.full_url) != _url_origin(target):
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "LLM API redirect to a different origin was blocked",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, target)


def build_topic_prompt(
    ths_hot_stocks: list[dict[str, Any]],
    dc_concepts: list[dict[str, Any]],
    industry_signals: list[dict[str, Any]] | None = None,
    latest_date: str = "",
) -> str:
    """Build a structured prompt for LLM topic classification.

    The LLM's job is to compress the observation-date hotspot data into a structured
    topic graph — NOT to recommend stocks. The output format is a JSON
    list of topics with weights.
    """
    prompt_lines = [
        "你是一个 A 股市场主题识别助手。你的任务不是选股，",
        "而是分析观测日收盘后可用的热点数据，输出下一交易时段的主题空间。",
        "",
        f"观测日期（EOD 数据截止日）: {latest_date}",
        "",
        "## 输入数据",
        "",
    ]

    if ths_hot_stocks:
        prompt_lines.append("### 同花顺热榜（观测日热门股票）")
        prompt_lines.append("| 排名 | 代码 | 名称 | 热度 | 所属概念 | 涨幅% |")
        prompt_lines.append("|------|------|------|------|----------|-------|")
        for s in ths_hot_stocks[:50]:
            prompt_lines.append(
                f"| {s.get('rank', '')} | {s.get('ts_code', '')} "
                f"| {s.get('ts_name', '')} | {s.get('hot', '')} "
                f"| {s.get('concept', '')} | {s.get('pct_change', '')} |"
            )
        prompt_lines.append("")

    if dc_concepts:
        prompt_lines.append("### 东方财富概念板块（观测日热门概念）")
        prompt_lines.append("| 名称 | 涨幅% | 强度 | 领涨股 | 涨停数 |")
        prompt_lines.append("|------|-------|------|--------|--------|")
        for c in dc_concepts[:30]:
            prompt_lines.append(
                f"| {c.get('name', '')} | {c.get('pct_change', '')} "
                f"| {c.get('strength', '')} | {c.get('lead_stock', '')} "
                f"| {c.get('z_t_num', '')} |"
            )
        prompt_lines.append("")

    if industry_signals:
        prompt_lines.append("### ETF 行业轮动信号（行业加权信号）")
        prompt_lines.append("| 排名 | 行业 | 权重 |")
        prompt_lines.append("|------|------|------|")
        for ind in industry_signals[:15]:
            prompt_lines.append(
                f"| {ind.get('rank', '')} | {ind.get('industry', '')} | {ind.get('weight', '')} |"
            )
        prompt_lines.append("")

    prompt_lines.extend(
        [
            "## 输出要求",
            "",
            "请输出一个 JSON 数组，每个元素包含：",
            '  - "topic": 主题名称（中文，例如 "AI医疗"、"半导体国产替代"）',
            '  - "weight": 主题置信度/热度权重，0-1 之间的浮点数',
            '  - "reasoning": 一句话说明为什么这个主题在观测日重要',
            '  - "related_concepts": 相关的概念板块名称列表',
            '  - "source_signals": 数据来源标记 ["ths_hot", "dc_concept", "etf_rotation"]',
            "",
            "约束：",
            "- 输出 3-5 个主题",
            "- weight 总和不一定为 1，这代表下一交易时段候选主题空间的分布",
            "- 不要选没有可交易标的的宏观主题",
            "- 不要编造数据——只基于上面提供的信息",
            "",
            "只输出 JSON 数组，不要加 markdown 代码块标记",
            "```json 和 ``` 都不要出现，直接输出 [",
        ]
    )

    return "\n".join(prompt_lines)


def _normalize_topics(payload: object) -> list[dict[str, Any]]:
    """Normalize an exact topic schema; reject the entire payload on any drift."""
    if not isinstance(payload, list) or not payload:
        return []

    topics: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or set(item) != _TOPIC_FIELDS:
            return []

        topic = item.get("topic")
        weight = item.get("weight")
        reasoning = item.get("reasoning")
        related_concepts = item.get("related_concepts")
        source_signals = item.get("source_signals")
        if not isinstance(topic, str) or not topic.strip():
            return []
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            return []
        if not 0.0 <= float(weight) <= 1.0:
            return []
        if not isinstance(reasoning, str):
            return []
        if (
            not related_concepts
            or not isinstance(related_concepts, list)
            or not all(isinstance(value, str) and bool(value.strip()) for value in related_concepts)
        ):
            return []
        if (
            not source_signals
            or not isinstance(source_signals, list)
            or not all(isinstance(value, str) and bool(value.strip()) for value in source_signals)
        ):
            return []

        topics.append(
            {
                "topic": topic.strip(),
                "weight": float(weight),
                "reasoning": reasoning,
                "related_concepts": related_concepts,
                "source_signals": source_signals,
            }
        )
    return topics


def _concept_values(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = re.split(r"[、,+，/|;；]+", value)
    if not isinstance(decoded, list):
        decoded = [decoded]
    return [str(item).strip() for item in decoded if str(item).strip()]


def _topic_vocabulary(
    ths_hot_stocks: list[dict[str, Any]],
    dc_concepts: list[dict[str, Any]],
) -> dict[str, str]:
    vocabulary: dict[str, str] = {}
    for row in dc_concepts:
        concept = str(row.get("name", "")).strip()
        if concept:
            vocabulary.setdefault(canonicalize_concept(concept).casefold(), concept)
    for row in ths_hot_stocks:
        for concept in _concept_values(row.get("concept")):
            vocabulary.setdefault(canonicalize_concept(concept).casefold(), concept)
    return vocabulary


def _available_topic_sources(
    ths_hot_stocks: list[dict[str, Any]],
    dc_concepts: list[dict[str, Any]],
    industry_signals: list[dict[str, Any]] | None,
) -> set[str]:
    sources: set[str] = {"ths_hot"} if ths_hot_stocks else set()
    for row in dc_concepts:
        source = str(row.get("source_signal") or "dc_concept")
        if source in _ALLOWED_SOURCE_SIGNALS:
            sources.add(source)
    if industry_signals:
        sources.add("etf_rotation")
    return sources


def _stock_identifiers(
    ths_hot_stocks: list[dict[str, Any]],
    dc_concepts: list[dict[str, Any]],
) -> set[str]:
    identifiers: set[str] = set()
    for row in ths_hot_stocks:
        for field in ("ts_code", "symbol", "code", "ts_name", "name"):
            value = str(row.get(field, "")).strip()
            if value:
                identifiers.add(value.casefold())
    for row in dc_concepts:
        for field in ("lead_stock", "lead_stock_name", "lead_stock_code"):
            value = str(row.get(field, "")).strip()
            if value:
                identifiers.add(value.casefold())
    return identifiers


def _is_stock_identifier(value: str, identifiers: set[str]) -> bool:
    stripped = value.strip()
    return bool(_STOCK_CODE_PATTERN.fullmatch(stripped)) or stripped.casefold() in identifiers


def _validated_topic_sources(sources: list[str], available_sources: set[str]) -> list[str]:
    for source in sources:
        if source not in _ALLOWED_SOURCE_SIGNALS:
            raise TopicValidationError(f"unsupported topic source: {source!r}")
        if source not in available_sources:
            raise TopicValidationError(
                f"topic source is unavailable for this observation: {source!r}"
            )
    return list(dict.fromkeys(sources))


def validate_and_sanitize_topics(
    payload: object,
    *,
    ths_hot_stocks: list[dict[str, Any]],
    dc_concepts: list[dict[str, Any]],
    industry_signals: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Validate topics and bind concepts/sources to the current observation inputs."""
    topics = _normalize_topics(payload)
    if not topics:
        raise TopicValidationError("topics must be a non-empty array using the exact schema")

    vocabulary = _topic_vocabulary(ths_hot_stocks, dc_concepts)
    available_sources = _available_topic_sources(
        ths_hot_stocks,
        dc_concepts,
        industry_signals,
    )
    stock_identifiers = _stock_identifiers(ths_hot_stocks, dc_concepts)

    sanitized: list[dict[str, Any]] = []
    for topic in topics:
        if _is_stock_identifier(topic["topic"], stock_identifiers):
            raise TopicValidationError(
                f"topic must not be a stock code or company name: {topic['topic']!r}"
            )
        related: list[str] = []
        for concept in topic["related_concepts"]:
            if _is_stock_identifier(concept, stock_identifiers):
                raise TopicValidationError(
                    f"related concept must not be a stock code or company name: {concept!r}"
                )
            known = vocabulary.get(canonicalize_concept(concept).casefold())
            if known is None:
                raise TopicValidationError(
                    f"related concept is not in the observation vocabulary: {concept!r}"
                )
            if known not in related:
                related.append(known)
        sources = _validated_topic_sources(topic["source_signals"], available_sources)
        sanitized.append(
            {
                **topic,
                "related_concepts": related,
                "source_signals": sources,
            }
        )
    return sanitized


def parse_topic_response(response_text: str) -> list[dict[str, Any]]:
    """Parse the LLM response into a structured topic list.

    Tries JSON parse first; falls back to extracting from markdown code blocks.
    """
    text = response_text.strip()

    # Remove markdown code block markers if present
    if text.startswith("```json"):
        text = text.removeprefix("```json")
    elif text.startswith("```"):
        text = text.removeprefix("```")
    if text.endswith("```"):
        text = text.removesuffix("```")
    text = text.strip()

    try:
        topics = _normalize_topics(json.loads(text))
        if topics:
            return topics
    except json.JSONDecodeError:
        pass

    # Fallback: try to find a JSON array in the text
    match = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
    if match:
        try:
            topics = _normalize_topics(json.loads(match.group()))
            if topics:
                return topics
        except json.JSONDecodeError:
            pass

    # Last resort: return empty
    return []


def _resolve_llm_endpoint_and_key() -> tuple[str, str]:
    configured_url = _os.environ.get("LLM_API_URL")
    api_url = configured_url or _DEEPSEEK_API_URL
    parsed = urllib.parse.urlparse(api_url)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise RuntimeError("LLM_API_URL contains an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("LLM_API_URL must be an HTTPS URL without embedded credentials")

    generic_key = _os.environ.get("LLM_API_KEY")
    if parsed.hostname.casefold() == _DEEPSEEK_HOST:
        api_key = generic_key or _os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("No LLM API key configured. Set LLM_API_KEY or DEEPSEEK_API_KEY.")
    else:
        api_key = generic_key
        if not api_key:
            raise RuntimeError("Custom LLM_API_URL requires an independent LLM_API_KEY.")
    return api_url.rstrip("/"), api_key


def _decode_llm_content(body: bytes) -> str:
    if len(body) > _MAX_LLM_RESPONSE_BYTES:
        raise RuntimeError("LLM API response exceeded the size limit")
    try:
        result = json.loads(body.decode("utf-8"))
        choices = result["choices"]
        message = choices[0]["message"]
        content = message["content"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("LLM API returned an invalid response schema") from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM API returned an empty response")
    return content


def _call_llm_for_topics(prompt: str) -> str:
    """Call the LLM to classify topics.

    Supports:
    1. OpenAI-compatible API via LLM_API_KEY or DEEPSEEK_API_KEY
    2. Optional endpoint/model overrides via LLM_API_URL / LLM_MODEL
    3. Direct HTTP call when running standalone
    """
    api_url, api_key = _resolve_llm_endpoint_and_key()
    model = _os.environ.get("LLM_MODEL", "deepseek-reasoner")

    payload = json.dumps(
        {
            "model": model,
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
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{api_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        opener = urllib.request.build_opener(_SameOriginRedirectHandler())
        with opener.open(req, timeout=120) as resp:
            body = resp.read(_MAX_LLM_RESPONSE_BYTES + 1)
            return _decode_llm_content(body)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        raise RuntimeError(f"LLM API call failed: {e}") from e


class TopicClassifier:
    """Classify observation-date hotspot data into a structured topic graph.

    This class encapsulates the LLM call for topic classification.
    It does NOT select stocks — it only outputs a topic/theme space.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def classify(
        self,
        ths_hot_stocks: list[dict[str, Any]],
        dc_concepts: list[dict[str, Any]],
        industry_signals: list[dict[str, Any]] | None = None,
        latest_date: str = "",
    ) -> list[dict[str, Any]]:
        """Run topic classification.

        Returns a list of topics:
          [{"topic": "AI医疗", "weight": 0.32, "reasoning": "...",
            "related_concepts": [...], "source_signals": [...]}]
        """
        if not self.enabled:
            return self._fallback_topics(ths_hot_stocks, dc_concepts)

        prompt = build_topic_prompt(ths_hot_stocks, dc_concepts, industry_signals, latest_date)

        try:
            response = _call_llm_for_topics(prompt)
            topics = parse_topic_response(response)

            if topics:
                return validate_and_sanitize_topics(
                    topics,
                    ths_hot_stocks=ths_hot_stocks,
                    dc_concepts=dc_concepts,
                    industry_signals=industry_signals,
                )
        except (RuntimeError, OSError, TopicValidationError):
            pass

        # LLM failed or returned unusable output; use fallback
        return self._fallback_topics(ths_hot_stocks, dc_concepts)

    @staticmethod
    def _fallback_topics(
        ths_hot_stocks: list[dict[str, Any]],
        dc_concepts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Fallback: extract topics from concept names in the data."""
        topics: list[dict[str, Any]] = []
        seen: set[str] = set()

        # From dc_concepts — sort by hot, take top 5
        sorted_dc = sorted(dc_concepts, key=lambda c: float(c.get("hot", 0)), reverse=True)
        strengths = [
            min(float(c.get("strength", 0)), 1000.0) for c in sorted_dc if c.get("strength")
        ]
        max_strength = max(strengths) if strengths else 1.0

        for c in sorted_dc[:5]:
            name = c.get("name", "")
            if name and name not in seen:
                seen.add(name)
                raw_strength = min(float(c.get("strength", 0)), 1000.0)
                topics.append(
                    {
                        "topic": name,
                        "weight": round(min(raw_strength / max_strength, 1.0), 3),
                        "reasoning": f"概念板块 {name} 在观测日领涨",
                        "related_concepts": [name],
                        "source_signals": [str(c.get("source_signal") or "dc_concept")],
                    }
                )

        # From ths_hot stock concepts — parse JSON array format
        concept_freq: dict[str, float] = {}
        for s in ths_hot_stocks:
            concept_str = s.get("concept", "")
            if not concept_str:
                continue
            try:
                concepts = json.loads(concept_str) if isinstance(concept_str, str) else concept_str
            except (json.JSONDecodeError, TypeError):
                concepts = [c.strip() for c in concept_str.split(",") if c.strip()]
            if not isinstance(concepts, list):
                concepts = [concepts]
            for c in concepts:
                c = str(c).strip()
                if c:
                    concept_freq[c] = concept_freq.get(c, 0) + 1

        for concept, freq in sorted(concept_freq.items(), key=lambda x: -x[1])[:5]:
            if concept not in seen and freq >= 2:
                seen.add(concept)
                topics.append(
                    {
                        "topic": concept,
                        "weight": min(freq / 10.0, 1.0),
                        "reasoning": f"同花顺热榜中 {freq} 只股票关联此概念",
                        "related_concepts": [concept],
                        "source_signals": ["ths_hot"],
                    }
                )

        return topics[:5]
