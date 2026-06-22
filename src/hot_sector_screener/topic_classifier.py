from __future__ import annotations

import json
import os as _os
import re
import urllib.error
import urllib.request
from typing import Any


def build_topic_prompt(
    ths_hot_stocks: list[dict[str, Any]],
    dc_concepts: list[dict[str, Any]],
    industry_signals: list[dict[str, Any]] | None = None,
    latest_date: str = "",
) -> str:
    """Build a structured prompt for LLM topic classification.

    The LLM's job is to compress today's hotspot data into a structured
    topic graph — NOT to recommend stocks. The output format is a JSON
    list of topics with weights.
    """
    prompt_lines = [
        "你是一个 A 股市场主题识别助手。你的任务不是选股，",
        "而是分析今天开盘前的热点数据，输出今天市场最可能的主题空间。",
        "",
        f"今天的日期: {latest_date}",
        "",
        "## 输入数据",
        "",
    ]

    if ths_hot_stocks:
        prompt_lines.append("### 同花顺热榜（今日热门股票）")
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
        prompt_lines.append("### 东方财富概念板块（今日热门概念）")
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
            '  - "reasoning": 一句话说明为什么这个主题今天重要',
            '  - "related_concepts": 相关的概念板块名称列表',
            '  - "source_signals": 数据来源标记 ["ths_hot", "dc_concept", "etf_rotation"]',
            "",
            "约束：",
            "- 输出 3-5 个主题",
            "- weight 总和不一定为 1，这代表当天可交易主题空间的分布",
            "- 不要选没有可交易标的的宏观主题",
            "- 不要编造数据——只基于上面提供的信息",
            "",
            "只输出 JSON 数组，不要加 markdown 代码块标记",
            "```json 和 ``` 都不要出现，直接输出 [",
        ]
    )

    return "\n".join(prompt_lines)


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
        topics = json.loads(text)
        if isinstance(topics, list):
            return topics
    except json.JSONDecodeError:
        pass

    # Fallback: try to find a JSON array in the text
    match = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
    if match:
        try:
            topics = json.loads(match.group())
            if isinstance(topics, list):
                return topics
        except json.JSONDecodeError:
            pass

    # Last resort: return empty
    return []


def _call_llm_for_topics(prompt: str) -> str:
    """Call the LLM to classify topics.

    Supports:
    1. OpenAI-compatible API via LLM_API_KEY / LLM_API_URL / LLM_MODEL
    2. Direct HTTP call when running standalone
    """
    api_key = _os.environ.get("LLM_API_KEY") or _os.environ.get("OPENAI_API_KEY")
    api_url = _os.environ.get("LLM_API_URL") or "https://api.deepseek.com/v1"
    model = _os.environ.get("LLM_MODEL", "deepseek-reasoner")

    if not api_key:
        raise RuntimeError(
            "No LLM API key configured. Set LLM_API_KEY or OPENAI_API_KEY. "
            "When running inside Hermes Agent, pass through the session's model."
        )

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
        f"{api_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        raise RuntimeError(f"LLM API call failed: {e}") from e


class TopicClassifier:
    """Classify today's hotspot data into structured topic graph.

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
                return topics
        except (RuntimeError, OSError):
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

        # From dc_concepts — compute max strength for normalization
        strengths = [float(c.get("strength", 0)) for c in dc_concepts if c.get("strength")]
        max_strength = max(strengths) if strengths else 1.0

        for c in dc_concepts[:5]:
            name = c.get("name", "")
            if name and name not in seen:
                seen.add(name)
                raw_strength = float(c.get("strength", 0))
                topics.append(
                    {
                        "topic": name,
                        "weight": round(min(raw_strength / max_strength, 1.0), 3),
                        "reasoning": f"概念板块 {name} 今日领涨",
                        "related_concepts": [name],
                        "source_signals": ["dc_concept"],
                    }
                )

        # From ths_hot stock concepts
        concept_freq: dict[str, float] = {}
        for s in ths_hot_stocks:
            concept_str = s.get("concept", "")
            for c in concept_str.split(","):
                c = c.strip()
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
