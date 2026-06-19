from __future__ import annotations

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
      3. Topic → ETF → constituent stocks (when concept mapping doesn't cover)
      4. Deduplicate and score by relevance
    """

    def __init__(self, dc_cons_df: pd.DataFrame, kpl_cons_df: pd.DataFrame | None = None):
        self.dc_cons = dc_cons_df
        self.kpl_cons = kpl_cons_df if kpl_cons_df is not None else pd.DataFrame()

        # Build lookup: concept name → set of ts_code
        self._dc_lookup: dict[str, set[str]] = {}
        if not self.dc_cons.empty and "theme_code" in self.dc_cons.columns:
            for _, row in self.dc_cons.iterrows():
                key = str(row.get("theme_code", "")).strip()
                code = _normalize_ts_code(str(row.get("ts_code", "")))
                if key and code:
                    self._dc_lookup.setdefault(key, set()).add(code)

        # Also build lookup from kpl: con_name → set of ts_code
        self._kpl_lookup: dict[str, set[str]] = {}
        if not self.kpl_cons.empty and "con_code" in self.kpl_cons.columns:
            for _, row in self.kpl_cons.iterrows():
                key = str(row.get("con_name", "")).strip()
                code = _normalize_ts_code(str(row.get("ts_code", "")))
                if key and code:
                    self._kpl_lookup.setdefault(key, set()).add(code)

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

        # 1. Match via related_concepts against dc_concept_cons
        for concept in related_concepts:
            # Try exact match in dc_lookup keys
            matched = False
            for dc_key, codes in self._dc_lookup.items():
                if concept.lower() in dc_key.lower():
                    for code in codes:
                        candidates[code] = candidates.get(code, 0.0) + 1.0
                    matched = True
            # Also try kpl lookup
            for kpl_key, codes in self._kpl_lookup.items():
                if concept.lower() in kpl_key.lower():
                    for code in codes:
                        candidates[code] = candidates.get(code, 0.0) + 0.8
                    matched = True

            if not matched:
                # Try matching concept name directly against stock concept field
                # in dc_cons (the 'industry' or 'name' fields may contain concept name)
                if not self.dc_cons.empty and "name" in self.dc_cons.columns:
                    match_mask = self.dc_cons["name"].str.contains(
                        concept, case=False, na=False
                    )
                    for _, row in self.dc_cons[match_mask].iterrows():
                        code = _normalize_ts_code(str(row.get("ts_code", "")))
                        if code:
                            candidates[code] = candidates.get(code, 0.0) + 0.5

        # 2. If topic_name matches a concept directly
        if topic_name and topic_name not in related_concepts:
            for dc_key, codes in self._dc_lookup.items():
                if topic_name.lower() in dc_key.lower():
                    for code in codes:
                        candidates[code] = candidates.get(code, 0.0) + 1.2

        # 3. If no concept mapping was found, use kpl descriptions
        if not candidates and not self.kpl_cons.empty and "desc" in self.kpl_cons.columns:
            match_mask = self.kpl_cons["desc"].str.contains(
                topic_name, case=False, na=False
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
            result.append({
                "ts_code": code,
                "name": name,
                "relevance": round(min(score / max(candidates.values()), 1.0), 3),
                "source_topic": topic_name,
            })

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
    """Apply basic liquidity and price filters.

    This is a placeholder — in production it would join with daily_basic
    from DATA_PLATFORM_ROOT to check turnover, market cap, etc.
    """
    # In the real pipeline, this would:
    # 1. Load daily_basic for the relevant date
    # 2. Filter by amount rank percentile
    # 3. Filter by price range
    # 4. Exclude ST stocks
    # 5. Exclude stocks with suspicious trading patterns

    # For now, pass through (filters will be applied when daily_basic is available)
    return stocks
