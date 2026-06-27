"""Tests for the topic classifier."""

from __future__ import annotations

from hot_sector_screener.topic_classifier import (
    TopicClassifier,
    build_topic_prompt,
    parse_topic_response,
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

    def test_parse_valid_json(self):
        response = (
            '[{"topic": "AI医疗", "weight": 0.32, "reasoning": "test", '
            '"related_concepts": ["AI"], "source_signals": ["ths_hot"]}]'
        )
        topics = parse_topic_response(response)
        assert len(topics) == 1
        assert topics[0]["topic"] == "AI医疗"
        assert topics[0]["weight"] == 0.32

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
