from __future__ import annotations

import pandas as pd
import pytest

from hot_sector_screener.confidence import apply_candidate_confidence
from hot_sector_screener.stock_mapper import StockMapper, apply_liquidity_filter


class TestStockMapper:
    @pytest.fixture
    def sample_dc_cons(self):
        return pd.DataFrame(
            [
                {
                    "ts_code": "300308.SZ",
                    "name": "中际旭创",
                    "theme_code": "CPO",
                    "trade_date": "20260619",
                    "industry": "通信",
                    "hot_num": 5,
                },
                {
                    "ts_code": "300502.SZ",
                    "name": "新易盛",
                    "theme_code": "CPO",
                    "trade_date": "20260619",
                    "industry": "通信",
                    "hot_num": 4,
                },
                {
                    "ts_code": "688981.SH",
                    "name": "中芯国际",
                    "theme_code": "半导体",
                    "trade_date": "20260619",
                    "industry": "电子",
                    "hot_num": 3,
                },
                {
                    "ts_code": "002371.SZ",
                    "name": "北方华创",
                    "theme_code": "半导体",
                    "trade_date": "20260619",
                    "industry": "电子",
                    "hot_num": 3,
                },
            ]
        )

    def test_map_single_topic(self, sample_dc_cons):
        mapper = StockMapper(sample_dc_cons)
        topic = {
            "topic": "CPO光通信",
            "weight": 0.8,
            "related_concepts": ["CPO"],
            "source_signals": ["ths_hot"],
        }
        stocks = mapper.map_topic_to_stocks(topic, max_stocks=10)
        assert len(stocks) == 2
        codes = {s["ts_code"] for s in stocks}
        assert "300308.SZ" in codes
        assert "300502.SZ" in codes

    def test_map_multiple_topics(self, sample_dc_cons):
        mapper = StockMapper(sample_dc_cons)
        topics = [
            {"topic": "CPO", "related_concepts": ["CPO"]},
            {"topic": "半导体", "related_concepts": ["半导体"]},
        ]
        stocks = mapper.map_topics(topics, max_total=10)
        assert len(stocks) == 4
        codes = {s["ts_code"] for s in stocks}
        assert "300308.SZ" in codes
        assert "688981.SH" in codes

    def test_dedup(self, sample_dc_cons):
        mapper = StockMapper(sample_dc_cons)
        topics = [
            {"topic": "CPO", "related_concepts": ["CPO"]},
            {"topic": "光通信", "related_concepts": ["CPO"]},
        ]
        stocks = mapper.map_topics(topics, max_total=10)
        assert len(stocks) == 2  # same 2 stocks, no duplicates

    def test_kpl_mapping_uses_constituent_stock_code(self):
        kpl_cons = pd.DataFrame(
            [
                {
                    "ts_code": "000025.KP",
                    "name": "AI算力",
                    "con_name": "浪潮信息",
                    "con_code": "000977.SZ",
                    "desc": "服务器算力",
                },
                {
                    "ts_code": "000025.KP",
                    "name": "AI算力",
                    "con_name": "中科曙光",
                    "con_code": "603019.SH",
                    "desc": "服务器算力",
                },
            ]
        )
        mapper = StockMapper(pd.DataFrame(), kpl_cons)

        stocks = mapper.map_topic_to_stocks(
            {"topic": "AI算力", "related_concepts": ["AI算力"]},
            max_stocks=10,
        )

        codes = {s["ts_code"] for s in stocks}
        assert codes == {"000977.SZ", "603019.SH"}
        assert "000025.KP" not in codes
        assert {s["name"] for s in stocks} == {"浪潮信息", "中科曙光"}

    def test_free_form_topic_cannot_select_stock_through_kpl_description(self):
        kpl_cons = pd.DataFrame(
            [
                {
                    "ts_code": "000025.KP",
                    "name": "算力租赁",
                    "con_name": "浪潮信息",
                    "con_code": "000977.SZ",
                    "desc": "AI服务器供应商",
                },
            ]
        )
        mapper = StockMapper(pd.DataFrame(), kpl_cons)

        stocks = mapper.map_topic_to_stocks(
            {"topic": "AI服务器", "related_concepts": []},
            max_stocks=10,
        )

        assert stocks == []

    def test_company_name_disguised_as_related_concept_cannot_select_stock(
        self,
        sample_dc_cons,
    ):
        mapper = StockMapper(sample_dc_cons)

        stocks = mapper.map_topic_to_stocks(
            {
                "topic": "AI 精选",
                "weight": 1.0,
                "related_concepts": ["中际旭创"],
            },
            max_stocks=10,
        )

        assert stocks == []

    def test_hot_event_theme_maps_to_topic(self):
        hot_stocks = pd.DataFrame(
            [
                {
                    "ts_code": "603730.SH",
                    "name": "岱美股份",
                    "theme": "人形机器人、特斯拉",
                    "lu_desc": "人形机器人+汽车零部件",
                    "tag": "涨停",
                    "event_source": "limit_list_ths",
                }
            ]
        )
        mapper = StockMapper(pd.DataFrame(), hot_stocks_df=hot_stocks)

        stocks = mapper.map_topic_to_stocks(
            {"topic": "人形机器人", "weight": 0.8, "related_concepts": ["人形机器人"]},
            max_stocks=10,
        )

        assert [s["ts_code"] for s in stocks] == ["603730.SH"]
        assert "人形机器人" in stocks[0]["source_concepts"]

    def test_hot_event_rows_fill_seed_candidates(self):
        hot_stocks = pd.DataFrame(
            [
                {
                    "ts_code": "600990.SH",
                    "name": "四创电子",
                    "theme": "商业航天、低空经济",
                    "lu_desc": "商业航天",
                    "tag": "涨停",
                    "event_source": "kpl_list",
                }
            ]
        )
        limit_step = pd.DataFrame([{"ts_code": "603137.SH", "name": "恒尚节能", "nums": 4}])
        mapper = StockMapper(
            pd.DataFrame(),
            hot_stocks_df=hot_stocks,
            limit_step_df=limit_step,
        )

        stocks = mapper.map_topics([], max_total=10)

        codes = {s["ts_code"] for s in stocks}
        assert codes == {"600990.SH", "603137.SH"}
        assert all("今日涨停热度" in s["source_topics"] for s in stocks)

    def test_hot_event_source_concepts_reject_serialized_prose_and_statuses(self):
        hot_stocks = pd.DataFrame(
            [
                {
                    "ts_code": "000566.SZ",
                    "name": "海南海药",
                    "theme": "创投、AI医疗",
                    "lu_desc": (
                        '["猴痘概念"],1,2天2板,III期临床正按计划推进,'
                        '["肝炎概念","不构成投资建议","主要系营业收入"]'
                    ),
                    "tag": "1.5、涨停、中报预增",
                    "event_source": "limit_list_ths",
                }
            ]
        )

        stocks = StockMapper(pd.DataFrame(), hot_stocks_df=hot_stocks).map_topics([])

        assert len(stocks) == 1
        assert set(stocks[0]["source_concepts"]) == {"创投", "AI医疗"}
        assert set(stocks[0]["source_event_tags"]) == {"1.5", "涨停", "中报预增"}
        assert stocks[0]["source_event_reasons"] == [hot_stocks.loc[0, "lu_desc"]]

    def test_event_metadata_cannot_match_topics_or_change_breadth_and_ranking(self):
        base_rows = [
            {
                "ts_code": "000001.SZ",
                "name": "甲公司",
                "theme": "AI医疗",
                "pct_chg": 5.0,
                "bid_amount": 1000.0,
                "event_source": "limit_list_ths",
            },
            {
                "ts_code": "000002.SZ",
                "name": "乙公司",
                "theme": "AI医疗",
                "pct_chg": 4.0,
                "bid_amount": 900.0,
                "event_source": "limit_list_ths",
            },
        ]
        clean = pd.DataFrame(base_rows)
        noisy = pd.DataFrame(
            [
                {
                    **base_rows[0],
                    "tag": "涨停、中报预增",
                    "status": "一字板",
                    "limit_type": "T字板",
                    "lu_desc": "不构成投资建议，主营业务描述",
                    "rank_reason": "媒体热议",
                },
                {
                    **base_rows[1],
                    "tag": "炸板、低开",
                    "status": "开板",
                    "limit_type": "跌停",
                    "lu_desc": "另一段事件说明",
                    "rank_reason": "情绪降温",
                },
            ]
        )
        topic = {
            "topic": "AI医疗",
            "weight": 1.0,
            "related_concepts": ["AI医疗"],
        }

        clean_ranked = StockMapper(pd.DataFrame(), hot_stocks_df=clean).map_topic_to_stocks(topic)
        noisy_mapper = StockMapper(pd.DataFrame(), hot_stocks_df=noisy)
        noisy_ranked = noisy_mapper.map_topic_to_stocks(topic)

        assert [
            (row["ts_code"], row["score"], row["relevance"], row["source_concepts"])
            for row in noisy_ranked
        ] == [
            (row["ts_code"], row["score"], row["relevance"], row["source_concepts"])
            for row in clean_ranked
        ]
        assert (
            noisy_mapper.map_topic_to_stocks(
                {"topic": "事件文本", "weight": 1.0, "related_concepts": ["中报预增"]}
            )
            == []
        )

        clean_confidence = apply_candidate_confidence(clean_ranked)
        noisy_confidence = apply_candidate_confidence(noisy_ranked)
        assert [row["score"] for row in noisy_confidence] == [
            row["score"] for row in clean_confidence
        ]
        assert [row["confidence_components"]["source_breadth"] for row in noisy_confidence] == [
            row["confidence_components"]["source_breadth"] for row in clean_confidence
        ]

    def test_hot_event_valid_json_array_keeps_only_concept_tokens(self):
        hot_stocks = pd.DataFrame(
            [
                {
                    "ts_code": "603716.SH",
                    "name": "塞力医疗",
                    "theme": '["细胞免疫治疗", "创新药", "仅供参考", 1]',
                    "event_source": "kpl_list",
                }
            ]
        )

        stocks = StockMapper(pd.DataFrame(), hot_stocks_df=hot_stocks).map_topics([])

        assert set(stocks[0]["source_concepts"]) == {"细胞免疫治疗", "创新药"}

    def test_alias_concept_maps_to_canonical_constituents(self):
        dc_concepts = pd.DataFrame(
            [
                {
                    "theme_code": "AI_CHIP",
                    "name": "AI芯片",
                    "strength": 80,
                    "hot": 60,
                    "pct_change": 2.5,
                }
            ]
        )
        dc_cons = pd.DataFrame(
            [
                {
                    "ts_code": "688256.SH",
                    "name": "寒武纪",
                    "theme_code": "AI_CHIP",
                    "hot_num": 5,
                }
            ]
        )
        mapper = StockMapper(dc_cons, dc_concept_df=dc_concepts)

        stocks = mapper.map_topic_to_stocks(
            {"topic": "GPU", "weight": 0.8, "related_concepts": ["GPU概念"]},
            max_stocks=10,
        )

        assert [s["ts_code"] for s in stocks] == ["688256.SH"]
        assert stocks[0]["source_concepts"] == ["AI芯片"]

    def test_liquidity_filter_passthrough(self):
        stocks = [
            {"ts_code": "300308.SZ", "name": "中际旭创", "relevance": 0.9},
            {"ts_code": "300502.SZ", "name": "新易盛", "relevance": 0.7},
        ]
        filtered = apply_liquidity_filter(stocks)
        assert len(filtered) == 2

    def test_liquidity_filter_applies_price_st_amount_and_one_price_limits(self):
        stocks = [
            {"ts_code": "300308.SZ", "name": "中际旭创", "relevance": 1.0},
            {"ts_code": "300502.SZ", "name": "新易盛", "relevance": 0.9},
            {"ts_code": "000001.SZ", "name": "ST平安", "relevance": 0.8},
            {"ts_code": "600000.SH", "name": "浦发银行", "relevance": 0.7},
        ]
        daily = pd.DataFrame(
            [
                {
                    "ts_code": "300308.SZ",
                    "close": 120,
                    "amount": 5000,
                    "high": 125,
                    "low": 118,
                    "pct_chg": 3,
                },
                {
                    "ts_code": "300502.SZ",
                    "close": 210,
                    "amount": 6000,
                    "high": 215,
                    "low": 205,
                    "pct_chg": 2,
                },
                {
                    "ts_code": "000001.SZ",
                    "close": 12,
                    "amount": 7000,
                    "high": 12.5,
                    "low": 11.8,
                    "pct_chg": 1,
                },
                {
                    "ts_code": "600000.SH",
                    "close": 10,
                    "amount": 8000,
                    "high": 10,
                    "low": 10,
                    "pct_chg": 10,
                },
            ]
        )

        filtered = apply_liquidity_filter(
            stocks,
            daily_df=daily,
            min_amount_rank_pct=20,
            max_price=200,
            min_price=2,
            allow_st=False,
        )

        assert [s["ts_code"] for s in filtered] == ["300308.SZ"]
        assert filtered[0]["liquidity_score"] > 0
        assert filtered[0]["raw_relevance"] == 1.0
