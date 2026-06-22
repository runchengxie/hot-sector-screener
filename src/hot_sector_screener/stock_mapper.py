from __future__ import annotations

import re
from typing import Any

import pandas as pd


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


class StockMapper:
    """Deterministically map topics to candidate stocks.

    Mapping logic:
      1. Topic → related concept/theme names
      2. Concept/theme → constituent stocks (from dc_concept_cons / kpl_concept_cons)
      3. Deduplicate and score by relevance
    """

    def __init__(
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
        if not self.dc_cons.empty and "theme_code" in self.dc_cons.columns:
            for _, row in self.dc_cons.iterrows():
                key = str(row.get("theme_code", "")).strip()
                code = _normalize_ts_code(str(row.get("ts_code", "")))
                if key and code:
                    self._dc_code_lookup.setdefault(key, set()).add(code)

        # Build lookup: concept name → set of ts_code
        # Use dc_concept (which has name + theme_code) to bridge
        self._dc_name_lookup: dict[str, set[str]] = {}
        if not self.dc_concept.empty and "name" in self.dc_concept.columns:
            for _, row in self.dc_concept.iterrows():
                name = str(row.get("name", "")).strip()
                theme_code = str(row.get("theme_code", "")).strip()
                if name and theme_code and theme_code in self._dc_code_lookup:
                    self._dc_name_lookup[name] = self._dc_code_lookup[theme_code]

        # Also try name matching directly from dc_cons
        if not self.dc_cons.empty and "name" in self.dc_cons.columns:
            for _, row in self.dc_cons.iterrows():
                name = str(row.get("name", "")).strip()
                code = _normalize_ts_code(str(row.get("ts_code", "")))
                if name and code:
                    self._dc_name_lookup.setdefault(name, set()).add(code)

        # Build lookup from kpl: con_name → set of ts_code
        self._kpl_lookup: dict[str, set[str]] = {}
        if not self.kpl_cons.empty and "con_code" in self.kpl_cons.columns:
            for _, row in self.kpl_cons.iterrows():
                key = str(row.get("con_name", "")).strip()
                code = _normalize_ts_code(str(row.get("ts_code", "")))
                if key and code:
                    self._kpl_lookup.setdefault(key, set()).add(code)

    def _match_concept(self, concept_name: str) -> set[str]:
        """Match a concept name against all lookups, return matching ts_codes."""
        results: set[str] = set()

        # 1. Try exact match in dc_name_lookup
        if concept_name in self._dc_name_lookup:
            results |= self._dc_name_lookup[concept_name]

        # 2. Try fuzzy match in dc_name_lookup keys
        for name, codes in self._dc_name_lookup.items():
            if concept_name.lower() in name.lower() or name.lower() in concept_name.lower():
                results |= codes

        # 3. Try exact match in dc_code_lookup
        concept_upper = concept_name.upper().replace(" ", "_").replace("-", "_")
        for code_key, codes in self._dc_code_lookup.items():
            if concept_upper == code_key.upper():
                results |= codes

        # 4. Try kpl
        if concept_name in self._kpl_lookup:
            results |= self._kpl_lookup[concept_name]
        for kpl_key, codes in self._kpl_lookup.items():
            if concept_name.lower() in kpl_key.lower() or kpl_key.lower() in concept_name.lower():
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
        candidates: dict[str, float] = {}

        # 1. Match via related_concepts against dc_concept_cons + kpl_concept_cons
        for concept in related_concepts:
            codes = self._match_concept(concept)
            for code in codes:
                candidates[code] = candidates.get(code, 0.0) + 1.0

            # Also try matching stock names directly (fallback for broad terms)
            if not codes and not self.dc_cons.empty and "name" in self.dc_cons.columns:
                match_mask = self.dc_cons["name"].str.contains(
                    re.escape(concept), case=False, na=False
                )
                for _, row in self.dc_cons[match_mask].iterrows():
                    code = _normalize_ts_code(str(row.get("ts_code", "")))
                    if code:
                        candidates[code] = candidates.get(code, 0.0) + 0.5

        # 2. If topic_name matches a concept directly (and wasn't already in related_concepts)
        if topic_name and topic_name not in related_concepts:
            codes = self._match_concept(topic_name)
            for code in codes:
                candidates[code] = candidates.get(code, 0.0) + 1.2

        # 3. If no concept mapping was found, use kpl descriptions
        if not candidates and not self.kpl_cons.empty and "desc" in self.kpl_cons.columns:
            match_mask = self.kpl_cons["desc"].str.contains(
                re.escape(topic_name), case=False, na=False
            )
            if match_mask.any():
                for _, row in self.kpl_cons[match_mask].iterrows():
                    code = _normalize_ts_code(str(row.get("ts_code", "")))
                    if code:
                        candidates[code] = candidates.get(code, 0.0) + 0.7

        # 4. Sort by relevance and limit
        sorted_candidates = sorted(candidates.items(), key=lambda x: -x[1])

        result = []
        for code, score in sorted_candidates[:max_stocks]:
            # Try to get name from dc_cons
            name = ""
            if not self.dc_cons.empty:
                match = self.dc_cons[self.dc_cons["ts_code"] == code]
                if not match.empty:
                    name = str(match.iloc[0].get("name", ""))
            result.append(
                {
                    "ts_code": code,
                    "name": name,
                    "relevance": round(min(score / max(candidates.values()), 1.0), 3),
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
                    seen[code]["relevance"] = max(seen[code]["relevance"], stock["relevance"])
                    if stock["source_topic"] not in seen[code]["source_topics"]:
                        seen[code]["source_topics"].append(stock["source_topic"])
                else:
                    seen[code] = {
                        "ts_code": code,
                        "name": stock["name"],
                        "relevance": stock["relevance"],
                        "source_topics": [stock["source_topic"]],
                    }

        # Sort by relevance descending, limit
        sorted_stocks = sorted(seen.values(), key=lambda x: -x["relevance"])
        # Remove the renamed key
        for s in sorted_stocks:
            s.pop("source_topic", None)

        return sorted_stocks[:max_total]


def apply_liquidity_filter(
    stocks: list[dict[str, Any]],
    min_amount_rank_pct: float = 80.0,
    max_price: float = 200.0,
    min_price: float = 2.0,
) -> list[dict[str, Any]]:
    """Apply basic liquidity and price filters (pass-through stub).

    This is a placeholder — in production it would join with daily_basic
    from DATA_PLATFORM_ROOT to check turnover, market cap, etc.
    """
    return stocks
