from __future__ import annotations

import pandas as pd
import pytest

from hotspot_universe.stock_mapper import StockMapper, apply_liquidity_filter


class TestStockMapper:
    @pytest.fixture
    def sample_dc_cons(self):
        return pd.DataFrame([
            {"ts_code": "300308.SZ", "name": "中际旭创", "theme_code": "CPO",
             "trade_date": "20260619", "industry": "通信", "hot_num": 5},
            {"ts_code": "300502.SZ", "name": "新易盛", "theme_code": "CPO",
             "trade_date": "20260619", "industry": "通信", "hot_num": 4},
            {"ts_code": "688981.SH", "name": "中芯国际", "theme_code": "半导体",
             "trade_date": "20260619", "industry": "电子", "hot_num": 3},
            {"ts_code": "002371.SZ", "name": "北方华创", "theme_code": "半导体",
             "trade_date": "20260619", "industry": "电子", "hot_num": 3},
        ])

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

    def test_liquidity_filter_passthrough(self):
        stocks = [
            {"ts_code": "300308.SZ", "name": "中际旭创", "relevance": 0.9},
            {"ts_code": "300502.SZ", "name": "新易盛", "relevance": 0.7},
        ]
        filtered = apply_liquidity_filter(stocks)
        assert len(filtered) == 2
