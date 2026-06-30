from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

from .concept_registry import canonicalize_concept, expand_concept_terms


def _normalize_ts_code(code: str) -> str:
    """Normalize a stock code to ts_code format (e.g. '000002.SZ')."""
    s = str(code).strip().upper()
    if "." in s:
        return s
    if s.startswith(("5", "6")):
        return f"{s}.SH"
    if s.startswith(("0", "1", "2", "3")):
        return f"{s}.SZ"
    return s


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _is_st_name(name: str) -> bool:
    upper = str(name or "").strip().upper()
    return upper.startswith(("ST", "*ST")) or "退市" in upper


class StockMapper:
    """Deterministically map topics to candidate stocks.

    Mapping logic:
      1. Topic → related concept/theme names
      2. Concept/theme → constituent stocks (from dc_concept_cons / kpl_concept_cons)
      3. Deduplicate and score by relevance
    """

    def __init__(  # noqa: C901
        self,
        dc_cons_df: pd.DataFrame,
        kpl_cons_df: pd.DataFrame | None = None,
        dc_concept_df: pd.DataFrame | None = None,
    ):
        self.dc_cons = dc_cons_df
        self.kpl_cons = kpl_cons_df if kpl_cons_df is not None else pd.DataFrame()
        self.dc_concept = dc_concept_df if dc_concept_df is not None else pd.DataFrame()

        # Build lookup: theme_code → set of ts_code
        self._dc_code_lookup: dict[str, set[str]] = {}
        self._name_by_code: dict[str, str] = {}
        self._stock_hot_score: dict[str, float] = {}
        if not self.dc_cons.empty and "theme_code" in self.dc_cons.columns:
            for _, row in self.dc_cons.iterrows():
                key = str(row.get("theme_code", "")).strip()
                code = _normalize_ts_code(str(row.get("ts_code", "")))
                if key and code:
                    self._dc_code_lookup.setdefault(key, set()).add(code)
                    self._dc_code_lookup.setdefault(canonicalize_concept(key), set()).add(code)
                if code:
                    name = str(row.get("name", "")).strip()
                    if name:
                        self._name_by_code.setdefault(code, name)
                    hot_num = _safe_float(row.get("hot_num"))
                    if hot_num > 0:
                        self._stock_hot_score[code] = max(
                            self._stock_hot_score.get(code, 1.0),
                            1.0 + min(math.log1p(hot_num) / 5.0, 0.5),
                        )

        # Build lookup: concept name → set of ts_code
        # Use dc_concept (which has name + theme_code) to bridge
        self._dc_name_lookup: dict[str, set[str]] = {}
        self._concept_strength: dict[str, float] = {}
        if not self.dc_concept.empty and "name" in self.dc_concept.columns:
            for _, row in self.dc_concept.iterrows():
                name = str(row.get("name", "")).strip()
                theme_code = str(row.get("theme_code", "")).strip()
                strength = self._row_concept_strength(row)
                self._record_concept_strength(name, strength)
                self._record_concept_strength(theme_code, strength)
                if name and theme_code and theme_code in self._dc_code_lookup:
                    self._add_concept_codes(name, self._dc_code_lookup[theme_code])
                    self._add_concept_codes(theme_code, self._dc_code_lookup[theme_code])

        # Also try name matching directly from dc_cons
        if not self.dc_cons.empty and "name" in self.dc_cons.columns:
            for _, row in self.dc_cons.iterrows():
                name = str(row.get("name", "")).strip()
                code = _normalize_ts_code(str(row.get("ts_code", "")))
                if name and code:
                    self._dc_name_lookup.setdefault(name, set()).add(code)

        # Build lookup from kpl: con_name → set of con_code (stock code)
        self._kpl_lookup: dict[str, set[str]] = {}
        if not self.kpl_cons.empty and "con_code" in self.kpl_cons.columns:
            for _, row in self.kpl_cons.iterrows():
                key = str(row.get("con_name", "")).strip()
                code = _normalize_ts_code(str(row.get("con_code", "")))
                if key and code:
                    for term in expand_concept_terms(key):
                        self._kpl_lookup.setdefault(term, set()).add(code)
                    name = str(row.get("name", "")).strip()
                    if name:
                        self._name_by_code.setdefault(code, name)
                    hot_num = _safe_float(row.get("hot_num"))
                    if hot_num > 0:
                        self._stock_hot_score[code] = max(
                            self._stock_hot_score.get(code, 1.0),
                            1.0 + min(math.log1p(hot_num) / 5.0, 0.5),
                        )

    def _add_concept_codes(self, concept_name: str, codes: set[str]) -> None:
        for term in expand_concept_terms(concept_name):
            self._dc_name_lookup.setdefault(term, set()).update(codes)

    def _record_concept_strength(self, concept_name: str, score: float) -> None:
        if not concept_name:
            return
        for term in expand_concept_terms(concept_name):
            canonical = canonicalize_concept(term)
            self._concept_strength[canonical] = max(
                self._concept_strength.get(canonical, 1.0), score
            )

    @staticmethod
    def _row_concept_strength(row: pd.Series) -> float:
        raw = max(
            _safe_float(row.get("strength")),
            _safe_float(row.get("hot")),
            abs(_safe_float(row.get("pct_change"))),
        )
        if raw <= 0:
            return 1.0
        return min(max(0.75 + raw / 100.0, 0.75), 1.5)

    def _concept_strength_score(self, concept_name: str) -> float:
        return self._concept_strength.get(canonicalize_concept(concept_name), 1.0)

    def _stock_heat_score(self, code: str) -> float:
        return self._stock_hot_score.get(code, 1.0)

    def _match_concept(self, concept_name: str) -> set[str]:
        """Match a concept name against all lookups, return matching ts_codes."""
        results: set[str] = set()
        terms = expand_concept_terms(concept_name)

        # 1. Try exact match in dc_name_lookup
        for term in terms:
            if term in self._dc_name_lookup:
                results |= self._dc_name_lookup[term]

        # 2. Try fuzzy match in dc_name_lookup keys
        term_lowers = [term.lower() for term in terms]
        for name, codes in self._dc_name_lookup.items():
            name_lower = name.lower()
            if any(term in name_lower or name_lower in term for term in term_lowers):
                results |= codes

        # 3. Try exact match in dc_code_lookup
        for term in terms:
            concept_upper = term.upper().replace(" ", "_").replace("-", "_")
            for code_key, codes in self._dc_code_lookup.items():
                if concept_upper == code_key.upper():
                    results |= codes

        # 4. Try kpl
        for term in terms:
            if term in self._kpl_lookup:
                results |= self._kpl_lookup[term]
        for kpl_key, codes in self._kpl_lookup.items():
            kpl_lower = kpl_key.lower()
            if any(term in kpl_lower or kpl_lower in term for term in term_lowers):
                results |= codes

        return results

    def map_topic_to_stocks(
        self,
        topic: dict[str, Any],
        max_stocks: int = 25,
    ) -> list[dict[str, Any]]:
        """Map a single topic to candidate stocks.

        Returns list of: {"ts_code": "000002.SZ", "name": "...", "relevance": 1.0}
        """
        related_concepts = topic.get("related_concepts", [])
        topic_name = topic.get("topic", "")
        topic_weight = max(_safe_float(topic.get("weight"), 1.0), 0.1)
        candidates: dict[str, dict[str, Any]] = {}

        def add_candidate(code: str, score: float, concept: str, source: str) -> None:
            code = _normalize_ts_code(code)
            if not code:
                return
            entry = candidates.setdefault(
                code,
                {"score": 0.0, "source_concepts": set(), "match_sources": set()},
            )
            entry["score"] = float(entry["score"]) + score * self._stock_heat_score(code)
            entry["source_concepts"].add(canonicalize_concept(concept) or concept)
            entry["match_sources"].add(source)

        # 1. Match via related_concepts against dc_concept_cons + kpl_concept_cons
        for concept in related_concepts:
            codes = self._match_concept(concept)
            concept_score = self._concept_strength_score(str(concept))
            for code in codes:
                add_candidate(code, topic_weight * concept_score, str(concept), "related_concept")

            # Also try matching stock names directly (fallback for broad terms)
            if not codes and not self.dc_cons.empty and "name" in self.dc_cons.columns:
                match_mask = self.dc_cons["name"].str.contains(
                    re.escape(concept), case=False, na=False
                )
                for _, row in self.dc_cons[match_mask].iterrows():
                    code = _normalize_ts_code(str(row.get("ts_code", "")))
                    if code:
                        add_candidate(code, topic_weight * 0.5, str(concept), "stock_name")

        # 2. If topic_name matches a concept directly (and wasn't already in related_concepts)
        if topic_name and topic_name not in related_concepts:
            codes = self._match_concept(topic_name)
            concept_score = self._concept_strength_score(str(topic_name))
            for code in codes:
                add_candidate(
                    code,
                    topic_weight * concept_score * 1.2,
                    str(topic_name),
                    "topic_name",
                )

        # 3. If no concept mapping was found, use kpl descriptions
        if not candidates and not self.kpl_cons.empty and "desc" in self.kpl_cons.columns:
            match_mask = self.kpl_cons["desc"].str.contains(
                re.escape(topic_name), case=False, na=False
            )
            if match_mask.any():
                for _, row in self.kpl_cons[match_mask].iterrows():
                    code = _normalize_ts_code(str(row.get("con_code") or row.get("ts_code", "")))
                    if code:
                        add_candidate(code, topic_weight * 0.7, str(topic_name), "kpl_desc")

        # 4. Sort by relevance and limit
        sorted_candidates = sorted(candidates.items(), key=lambda item: -float(item[1]["score"]))
        max_score = max((float(item["score"]) for item in candidates.values()), default=1.0)

        result = []
        for code, data in sorted_candidates[:max_stocks]:
            # Try to get name from dc_cons
            score = float(data["score"])
            name = self._name_by_code.get(code, "")
            result.append(
                {
                    "ts_code": code,
                    "name": name,
                    "relevance": round(min(score / max_score, 1.0), 3),
                    "score": round(score, 4),
                    "source_concepts": sorted(data["source_concepts"]),
                    "match_sources": sorted(data["match_sources"]),
                    "source_topic": topic_name,
                }
            )

        return result

    def map_topics(
        self,
        topics: list[dict[str, Any]],
        max_stocks_per_topic: int = 25,
        max_total: int = 100,
    ) -> list[dict[str, Any]]:
        """Map multiple topics to a deduplicated stock list.

        Stocks can appear under multiple topics; the highest relevance is kept.
        """
        seen: dict[str, dict[str, Any]] = {}
        for topic in topics:
            stocks = self.map_topic_to_stocks(topic, max_stocks=max_stocks_per_topic)
            for stock in stocks:
                code = stock["ts_code"]
                if code in seen:
                    seen[code]["score"] += stock.get("score", stock["relevance"])
                    if stock["source_topic"] not in seen[code]["source_topics"]:
                        seen[code]["source_topics"].append(stock["source_topic"])
                    for concept in stock.get("source_concepts", []):
                        if concept not in seen[code]["source_concepts"]:
                            seen[code]["source_concepts"].append(concept)
                else:
                    seen[code] = {
                        "ts_code": code,
                        "name": stock["name"],
                        "score": stock.get("score", stock["relevance"]),
                        "relevance": stock["relevance"],
                        "source_topics": [stock["source_topic"]],
                        "source_concepts": list(stock.get("source_concepts", [])),
                    }

        max_score = max((float(item.get("score", 0.0)) for item in seen.values()), default=1.0)
        for item in seen.values():
            item["relevance"] = round(min(float(item.get("score", 0.0)) / max_score, 1.0), 3)

        sorted_stocks = sorted(seen.values(), key=lambda x: (-x["relevance"], -x["score"]))
        return sorted_stocks[:max_total]


def apply_liquidity_filter(
    stocks: list[dict[str, Any]],
    daily_df: pd.DataFrame | None = None,
    min_amount_rank_pct: float = 80.0,
    max_price: float = 200.0,
    min_price: float = 2.0,
    allow_st: bool = False,
) -> list[dict[str, Any]]:
    """Apply liquidity, price, ST, and one-price limit filters when daily data is available."""
    if not stocks:
        return []
    if daily_df is None or daily_df.empty or "ts_code" not in daily_df.columns:
        return stocks

    daily = daily_df.copy()
    daily["ts_code"] = daily["ts_code"].map(lambda code: _normalize_ts_code(str(code)))
    for col in ("amount", "close", "high", "low", "pct_chg"):
        if col in daily.columns:
            daily[col] = pd.to_numeric(daily[col], errors="coerce")
    if "amount" in daily.columns:
        daily["amount_rank_pct"] = daily["amount"].rank(pct=True, method="average") * 100
    else:
        daily["amount_rank_pct"] = 100.0

    by_code = daily.drop_duplicates("ts_code", keep="last").set_index("ts_code")
    filtered: list[dict[str, Any]] = []
    for stock in stocks:
        code = _normalize_ts_code(str(stock.get("ts_code", "")))
        if not code or code not in by_code.index:
            continue
        row = by_code.loc[code]
        name = str(stock.get("name") or row.get("name") or row.get("ts_name") or "")
        close = _safe_float(row.get("close"))
        amount_rank_pct = _safe_float(row.get("amount_rank_pct"), 100.0)

        if not allow_st and _is_st_name(name):
            continue
        if close <= 0 or close < min_price or close > max_price:
            continue
        if amount_rank_pct < min_amount_rank_pct:
            continue

        high = _safe_float(row.get("high"))
        low = _safe_float(row.get("low"))
        pct_chg = abs(_safe_float(row.get("pct_chg")))
        if high > 0 and low > 0 and abs(high - low) <= 1e-9 and pct_chg >= 9.5:
            continue

        liquidity_score = max(min(amount_rank_pct / 100.0, 1.0), 0.0)
        item = dict(stock)
        item["ts_code"] = code
        if name and not item.get("name"):
            item["name"] = name
        item["raw_relevance"] = item.get("relevance", 0.0)
        item["liquidity_score"] = round(liquidity_score, 3)
        item["amount_rank_pct"] = round(amount_rank_pct, 1)
        item["close"] = round(close, 2)
        item["relevance"] = round(_safe_float(item.get("relevance")) * liquidity_score, 3)
        filtered.append(item)

    return sorted(filtered, key=lambda item: (-float(item.get("relevance", 0.0)), item["ts_code"]))
