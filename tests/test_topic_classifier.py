"""Tests for the topic classifier."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest

from hot_sector_screener.topic_classifier import (
    TopicClassificationError,
    TopicClassifier,
    TopicValidationError,
    build_topic_prompt,
    parse_topic_response,
    validate_and_sanitize_topics,
)
from hot_sector_screener.topic_provider import (
    ProviderReceipt,
    ProviderResponse,
    TopicProviderError,
)


def _provider_response(
    content: str,
    *,
    provider_id: str = "test-gateway",
    model: str = "topic-classifier-v1",
    api_host: str = "classification.example.test",
) -> ProviderResponse:
    return ProviderResponse(
        content=content,
        receipt=ProviderReceipt(
            provider_id=provider_id,
            model=model,
            api_host=api_host,
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
        ),
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

    def test_classifier_rejects_invalid_llm_provenance_without_fallback(self):
        response = (
            '[{"topic": "AI 精选", "weight": 1.0, "reasoning": "test", '
            '"related_concepts": ["中际旭创", "GPU"], '
            '"source_signals": ["ths_hot", "dc_concept", "etf_rotation", "model_pick"]}]'
        )
        provider = Mock()
        provider.complete.return_value = _provider_response(response)
        classifier = TopicClassifier(provider=provider)
        with (
            patch.object(classifier, "_fallback_topics") as fallback_call,
            pytest.raises(TopicClassificationError, match="observation-bound"),
        ):
            classifier.classify(
                ths_hot_stocks=[],
                dc_concepts=[{"name": "AI芯片"}],
            )

        fallback_call.assert_not_called()

    def test_classifier_rejects_transport_failure_without_fallback(self):
        provider = Mock()
        provider.complete.side_effect = TopicProviderError("provider request failed")
        classifier = TopicClassifier(provider=provider)

        with (
            patch.object(classifier, "_fallback_topics") as fallback_call,
            pytest.raises(TopicClassificationError, match="provider request failed"),
        ):
            classifier.classify(
                ths_hot_stocks=[],
                dc_concepts=[{"name": "AI芯片"}],
            )

        fallback_call.assert_not_called()

    def test_classifier_records_receipt_only_after_validated_response(self):
        response = (
            '[{"topic": "AI算力", "weight": 0.8, "reasoning": "观测日热点", '
            '"related_concepts": ["AI芯片"], "source_signals": ["dc_concept"]}]'
        )
        provider = Mock()
        provider.complete.return_value = _provider_response(response)
        classifier = TopicClassifier(provider=provider)

        topics = classifier.classify(
            ths_hot_stocks=[],
            dc_concepts=[{"name": "AI芯片"}],
        )

        assert topics[0]["topic"] == "AI算力"
        assert topics[0]["reasoning"] == "观测日热点"
        assert classifier.last_provider_receipt == _provider_response(response).receipt

    @pytest.mark.parametrize(
        ("field", "unsafe_text"),
        [
            ("topic", "runtime＿ow ner 热点"),
            ("reasoning", "由 RANK－ENGINE V9 完成归类"),
            ("reasoning", "classification＿node.example.test"),
            ("reasoning", "详见 ｈｔｔｐｓ：／／news.example.test"),
            ("reasoning", "详见 news.example.tech/path"),
            ("reasoning", "详见 192.0.2.1/path"),
            ("reasoning", "API＿K E Y 已配置"),
            ("reasoning", "access k-e-y 元数据"),
            ("reasoning", "pro-vider meta-data"),
            ("reasoning", "模 型 标 识 信息"),
            ("reasoning", "response h-a-s-h 信息"),
            ("reasoning", "供 应 商 标 识 信息"),
            ("reasoning", "endpoint U-R-L 信息"),
            ("reasoning", f"{'市场热点' * 500}，包含 access to-k e n 元数据"),
        ],
    )
    def test_classifier_rejects_metadata_leaks_in_remote_public_text(
        self,
        field,
        unsafe_text,
    ):
        topic = {
            "topic": "AI算力",
            "weight": 0.8,
            "reasoning": "观测日热点",
            "related_concepts": ["AI芯片"],
            "source_signals": ["dc_concept"],
        }
        topic[field] = unsafe_text
        response = json.dumps([topic], ensure_ascii=False)
        provider = Mock()
        provider.complete.return_value = _provider_response(
            response,
            provider_id="runtime-owner",
            model="rank-engine-v9",
            api_host="classification-node.example.test",
        )
        classifier = TopicClassifier(provider=provider)

        with pytest.raises(TopicClassificationError) as exc_info:
            classifier.classify(
                ths_hot_stocks=[],
                dc_concepts=[{"name": "AI芯片"}],
            )

        assert str(exc_info.value) == "provider response failed public text safety validation"
        assert "runtime-owner" not in str(exc_info.value)
        assert "rank-engine-v9" not in str(exc_info.value)
        assert "classification-node" not in str(exc_info.value)
        assert classifier.last_provider_receipt is None

    @pytest.mark.parametrize(
        ("provider_id", "model", "unsafe_text"),
        [
            ("latticebird-runtime", "topic-engine-v9", "LATTICE＿BIRD 归类"),
            ("runtime-owner", "copperfalcon-family-v9", "Copper Falcon 归类"),
        ],
    )
    def test_classifier_rejects_distinctive_provider_and_model_components(
        self,
        provider_id,
        model,
        unsafe_text,
    ):
        response = json.dumps(
            [
                {
                    "topic": "AI算力",
                    "weight": 0.8,
                    "reasoning": unsafe_text,
                    "related_concepts": ["AI芯片"],
                    "source_signals": ["dc_concept"],
                }
            ],
            ensure_ascii=False,
        )
        provider = Mock()
        provider.complete.return_value = _provider_response(
            response,
            provider_id=provider_id,
            model=model,
            api_host="classification-node.example.test",
        )

        with pytest.raises(TopicClassificationError) as exc_info:
            TopicClassifier(provider=provider).classify(
                ths_hot_stocks=[],
                dc_concepts=[{"name": "AI芯片"}],
            )

        assert str(exc_info.value) == "provider response failed public text safety validation"
        assert provider_id not in str(exc_info.value)
        assert model not in str(exc_info.value)

    @pytest.mark.parametrize(
        "public_text",
        [
            "大模型应用",
            "模型推理",
            "头部供应商",
            "核心供应商",
            "Keyence产业链",
        ],
    )
    def test_classifier_preserves_legitimate_market_language(self, public_text):
        response = json.dumps(
            [
                {
                    "topic": public_text,
                    "weight": 0.8,
                    "reasoning": f"{public_text}在观测日热度上升",
                    "related_concepts": ["AI芯片"],
                    "source_signals": ["dc_concept"],
                }
            ],
            ensure_ascii=False,
        )
        provider = Mock()
        provider.complete.return_value = _provider_response(
            response,
            provider_id="runtime-owner",
            model="rank-engine-v9",
            api_host="classification-node.example.test",
        )

        topics = TopicClassifier(provider=provider).classify(
            ths_hot_stocks=[],
            dc_concepts=[{"name": "AI芯片"}],
        )

        assert topics[0]["topic"] == public_text
        assert topics[0]["reasoning"] == f"{public_text}在观测日热度上升"

    def test_api_host_components_are_not_used_as_dynamic_public_text_needles(self):
        response = (
            '[{"topic":"Example测试主题","weight":0.8,"reasoning":"观测日热度上升",'
            '"related_concepts":["AI芯片"],"source_signals":["dc_concept"]}]'
        )
        provider = Mock()
        provider.complete.return_value = _provider_response(
            response,
            provider_id="runtime-owner",
            model="rank-engine-v9",
            api_host="routing.example.test",
        )

        topics = TopicClassifier(provider=provider).classify(
            ths_hot_stocks=[],
            dc_concepts=[{"name": "AI芯片"}],
        )

        assert topics[0]["topic"] == "Example测试主题"

    def test_observation_bound_related_concept_is_not_treated_as_free_form_metadata(self):
        response = (
            '[{"topic":"智能应用","weight":0.8,"reasoning":"观测日热度上升",'
            '"related_concepts":["大模型"],"source_signals":["dc_concept"]}]'
        )
        provider = Mock()
        provider.complete.return_value = _provider_response(response)

        topics = TopicClassifier(provider=provider).classify(
            ths_hot_stocks=[],
            dc_concepts=[{"name": "大模型"}],
        )

        assert topics[0]["related_concepts"] == ["大模型"]

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
