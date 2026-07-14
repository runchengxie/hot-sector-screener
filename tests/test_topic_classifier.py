"""Tests for the topic classifier."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hot_sector_screener.topic_classifier import (
    TopicClassifier,
    TopicValidationError,
    build_topic_prompt,
    parse_topic_response,
    validate_and_sanitize_topics,
)


class TestTopicClassifier:
    def test_build_prompt_has_all_sections(self):
        stocks = [
            {
                "ts_code": "300308.SZ",
                "ts_name": "中际旭创",
                "rank": "1",
                "hot": "99",
                "concept": "CPO,光通信",
                "pct_change": "10.0",
            }
        ]
        concepts = [
            {
                "name": "CPO概念",
                "pct_change": "5.2",
                "strength": "8.5",
                "lead_stock": "中际旭创",
                "z_t_num": "5",
            }
        ]
        prompt = build_topic_prompt(stocks, concepts, latest_date="2026-06-19")

        assert "2026-06-19" in prompt
        assert "同花顺热榜" in prompt
        assert "中际旭创" in prompt
        assert "CPO概念" in prompt
        assert "东方财富概念板块" in prompt
        assert "JSON" in prompt

    def test_build_prompt_limits_llm_to_topic_classification(self):
        prompt = build_topic_prompt([], [], latest_date="2026-06-19")

        assert "你的任务不是选股" in prompt
        assert '"topic"' in prompt
        assert '"weight"' in prompt
        assert '"related_concepts"' in prompt
        assert '"ts_code"' not in prompt
        assert '"stock"' not in prompt

    def test_parse_valid_json(self):
        response = (
            '[{"topic": "AI医疗", "weight": 0.32, "reasoning": "test", '
            '"related_concepts": ["AI"], "source_signals": ["ths_hot"]}]'
        )
        topics = parse_topic_response(response)
        assert len(topics) == 1
        assert topics[0]["topic"] == "AI医疗"
        assert topics[0]["weight"] == 0.32

    def test_parse_rejects_fields_outside_topic_contract(self):
        response = (
            '[{"topic": "AI医疗", "weight": 0.32, "reasoning": "test", '
            '"related_concepts": ["AI"], "source_signals": ["ths_hot"], '
            '"ts_code": "300308.SZ", "stock_picks": ["中际旭创"]}]'
        )

        topics = parse_topic_response(response)

        assert topics == []

    def test_parse_rejects_stock_pick_payload_without_topic_schema(self):
        response = '[{"ts_code": "300308.SZ", "rank": 1, "reasoning": "AI 精选"}]'

        assert parse_topic_response(response) == []

    def test_classifier_rejects_invalid_llm_provenance_and_uses_fallback(self):
        response = (
            '[{"topic": "AI 精选", "weight": 1.0, "reasoning": "test", '
            '"related_concepts": ["中际旭创", "GPU"], '
            '"source_signals": ["ths_hot", "dc_concept", "etf_rotation", "model_pick"]}]'
        )
        classifier = TopicClassifier()
        fallback = [
            {
                "topic": "fallback",
                "weight": 1.0,
                "reasoning": "invalid LLM response",
                "related_concepts": ["AI芯片"],
                "source_signals": ["dc_concept"],
            }
        ]
        with (
            patch(
                "hot_sector_screener.topic_classifier._call_llm_for_topics",
                return_value=response,
            ),
            patch.object(classifier, "_fallback_topics", return_value=fallback) as fallback_call,
        ):
            topics = classifier.classify(
                ths_hot_stocks=[],
                dc_concepts=[{"name": "AI芯片"}],
            )

        assert topics == fallback
        fallback_call.assert_called_once()

    def test_parse_markdown_code_block(self):
        response = (
            "```json\n"
            '[{"topic": "半导体", "weight": 0.25, "reasoning": "test", '
            '"related_concepts": ["半导体"], "source_signals": ["ths_hot"]}]\n'
            "```"
        )
        topics = parse_topic_response(response)
        assert len(topics) == 1
        assert topics[0]["topic"] == "半导体"

    def test_parse_empty_returns_empty_list(self):
        topics = parse_topic_response("")
        assert topics == []

    def test_parse_gibberish_returns_empty_list(self):
        topics = parse_topic_response("Sorry, I cannot help with that.")
        assert topics == []

    def test_fallback_topics_sorts_dc_concepts_by_hot(self):
        topics = TopicClassifier(enabled=False).classify(
            ths_hot_stocks=[],
            dc_concepts=[
                {"name": "低热度", "hot": "1", "strength": "10"},
                {"name": "高热度", "hot": "99", "strength": "5"},
            ],
        )

        assert topics[0]["topic"] == "高热度"

    def test_fallback_topics_parses_json_concept_arrays(self):
        topics = TopicClassifier(enabled=False).classify(
            ths_hot_stocks=[
                {"concept": '["AI算力", "CPO"]'},
                {"concept": '["AI算力", "机器人"]'},
                {"concept": "CPO,光通信"},
            ],
            dc_concepts=[],
        )

        by_topic = {topic["topic"]: topic for topic in topics}
        assert by_topic["AI算力"]["weight"] == 0.2
        assert by_topic["CPO"]["weight"] == 0.2


def _valid_topic_payload(*, topic="AI算力", concept="GPU", source="dc_concept"):
    return [
        {
            "topic": topic,
            "weight": 0.8,
            "reasoning": "观测数据中的主题",
            "related_concepts": [concept],
            "source_signals": [source],
        }
    ]


def test_public_topic_validator_canonicalizes_known_aliases():
    topics = validate_and_sanitize_topics(
        _valid_topic_payload(),
        ths_hot_stocks=[],
        dc_concepts=[{"name": "AI芯片"}],
        industry_signals=None,
    )

    assert topics[0]["related_concepts"] == ["AI芯片"]


@pytest.mark.parametrize(
    ("topic", "concept", "source", "message"),
    [
        ("AI精选", "中际旭创", "ths_hot", "stock code or company name"),
        ("AI精选", "300308.SZ", "ths_hot", "stock code or company name"),
        ("中际旭创", "AI芯片", "ths_hot", "stock code or company name"),
        ("AI精选", "不存在的伪概念", "ths_hot", "observation vocabulary"),
        ("AI精选", "AI芯片", "model_pick", "unsupported topic source"),
        ("AI精选", "AI芯片", "etf_rotation", "source is unavailable"),
    ],
)
def test_public_topic_validator_rejects_stock_picks_and_unproven_sources(
    topic,
    concept,
    source,
    message,
):
    with pytest.raises(TopicValidationError, match=message):
        validate_and_sanitize_topics(
            _valid_topic_payload(topic=topic, concept=concept, source=source),
            ths_hot_stocks=[
                {
                    "ts_code": "300308.SZ",
                    "ts_name": "中际旭创",
                    "concept": '["AI芯片"]',
                }
            ],
            dc_concepts=[{"name": "AI芯片"}],
            industry_signals=None,
        )
