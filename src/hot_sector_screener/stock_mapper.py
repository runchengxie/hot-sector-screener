from __future__ import annotations

import json
import math
import re
from typing import Any

import pandas as pd

from .concept_registry import canonicalize_concept, expand_concept_terms

_CONCEPT_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff（）()·._ -]+$")
_NUMERIC_TOKEN_PATTERN = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_EVENT_STATUS_PATTERN = re.compile(
    r"^(?:首板|涨停|炸板|跌停|一字板|T字板|天地板|地天板|"
    r"\d+天\d+板|\d+连板|连板\d*)$",
    re.IGNORECASE,
)
_NON_CONCEPT_PHRASES = (
    "不构成投资建议",
    "投资建议",
    "仅供参考",
    "公告为准",
    "上市公司公告",
    "主营业务",
    "营业收入",
    "市场份额",
    "计划推进",
    "主要受",
    "主要系",
    "主要因",
    "股价异常",
    "核查确认",
    "公司",
    "期临床",
)


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


def _is_valid_concept_token(value: Any) -> bool:
    """Accept short market-theme labels and reject prose/status/serialized debris."""
    token = str(value or "").strip()
    compact = re.sub(r"\s+", "", token)
    if not compact or compact.lower() == "nan":
        return False
    if len(compact) < 2 or len(compact) > 16:
        return False
    if _NUMERIC_TOKEN_PATTERN.fullmatch(compact) or _EVENT_STATUS_PATTERN.fullmatch(compact):
        return False
    if any(phrase in compact for phrase in _NON_CONCEPT_PHRASES):
        return False
    return _CONCEPT_TOKEN_PATTERN.fullmatch(token) is not None


def _split_concept_text(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []

    # Some upstream event fields contain a JSON array. Parse a valid array as a
    # structure, but fail closed when bracketed JSON is malformed instead of
    # leaking fragments such as `[\"猴痘概念\"` into the public contract.
    if text.startswith("[") or text.endswith("]"):
        try:
            decoded = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(decoded, list) or any(isinstance(item, (dict, list)) for item in decoded):
            return []
        parts = [item.strip() for item in decoded if isinstance(item, str)]
    elif any(marker in text for marker in "{}[]\"'"):
        return []
    else:
        parts = [part.strip() for part in re.split(r"[、,+，/|;；]+", text)]

    return [part for part in parts if _is_valid_concept_token(part)]


class StockMapper:
    """Deterministically map topics to candidate stocks.

    Mapping logic:
      1. Topic → related concept/theme names
      2. Concept/theme → constituent stocks (from dc_concept_cons / kpl_concept_cons)
      3. Same-day limit-up/hot-list rows add topical matches and heat seeds
      4. Deduplicate and score by relevance
    """

    def __init__(  # noqa: C901
        self,
        dc_cons_df: pd.DataFrame,
        kpl_cons_df: pd.DataFrame | None = None,
        dc_concept_df: pd.DataFrame | None = None,
        hot_stocks_df: pd.DataFrame | None = None,
        limit_step_df: pd.DataFrame | None = None,
        limit_cpt_df: pd.DataFrame | None = None,
    ):
        self.dc_cons = dc_cons_df
        self.kpl_cons = kpl_cons_df if kpl_cons_df is not None else pd.DataFrame()
        self.dc_concept = dc_concept_df if dc_concept_df is not None else pd.DataFrame()
        self.hot_stocks = hot_stocks_df if hot_stocks_df is not None else pd.DataFrame()
        self.limit_step = limit_step_df if limit_step_df is not None else pd.DataFrame()
        self.limit_cpt = limit_cpt_df if limit_cpt_df is not None else pd.DataFrame()

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
        self._event_lookup: dict[str, set[str]] = {}
        self._event_stock_concepts: dict[str, set[str]] = {}
        self._hot_seed_scores: dict[str, float] = {}
        self._hot_seed_sources: dict[str, set[str]] = {}
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

        # Build lookup from KPL concept name → constituent con_code (stock code).
        # In the platform schema, name is the concept and con_name is the stock name.
        self._kpl_lookup: dict[str, set[str]] = {}
        if not self.kpl_cons.empty and "con_code" in self.kpl_cons.columns:
            for _, row in self.kpl_cons.iterrows():
                key = str(row.get("name", "")).strip()
                code = _normalize_ts_code(str(row.get("con_code", "")))
                if key and code:
                    for term in expand_concept_terms(key):
                        self._kpl_lookup.setdefault(term, set()).add(code)
                    stock_name = str(row.get("con_name", "")).strip()
                    if stock_name:
                        self._name_by_code.setdefault(code, stock_name)
                    hot_num = _safe_float(row.get("hot_num"))
                    if hot_num > 0:
                        self._stock_hot_score[code] = max(
                            self._stock_hot_score.get(code, 1.0),
                            1.0 + min(math.log1p(hot_num) / 5.0, 0.5),
                        )

        if not self.hot_stocks.empty:
            for _, row in self.hot_stocks.iterrows():
                self._record_hot_stock(row)

        if not self.limit_step.empty and "ts_code" in self.limit_step.columns:
            for _, row in self.limit_step.iterrows():
                code = _normalize_ts_code(str(row.get("ts_code", "")))
                if not code:
                    continue
                name = str(row.get("name", "")).strip()
                if name:
                    self._name_by_code[code] = name
                score = 1.0 + min(_safe_float(row.get("nums")) / 8.0, 0.75)
                self._stock_hot_score[code] = max(self._stock_hot_score.get(code, 1.0), score)
                self._hot_seed_scores[code] = max(self._hot_seed_scores.get(code, 0.0), score)
                self._hot_seed_sources.setdefault(code, set()).add("limit_step")
                self._event_stock_concepts.setdefault(code, set()).add("连板天梯")

        if not self.limit_cpt.empty and "name" in self.limit_cpt.columns:
            for _, row in self.limit_cpt.iterrows():
                self._record_limit_concept(row)

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

    def _add_event_codes(self, concept_name: str, code: str) -> None:
        if not concept_name or not code:
            return
        for term in expand_concept_terms(concept_name):
            self._event_lookup.setdefault(term, set()).add(code)

    @staticmethod
    def _event_row_score(row: pd.Series) -> float:
        score = 0.45
        tag = str(row.get("tag", "") or "")
        limit_type = str(row.get("limit_type", "") or "")
        status = str(row.get("status", "") or "")
        if "涨停" in tag or "涨停" in limit_type:
            score += 0.45
        if "炸" in tag or "炸" in limit_type:
            score += 0.15
        if "T字" in status or "一字" in status:
            score += 0.1
        score += min(abs(_safe_float(row.get("pct_chg"))) / 20.0, 0.35)
        score += min(math.log1p(max(_safe_float(row.get("bid_amount")), 0.0)) / 30.0, 0.25)
        return score

    @staticmethod
    def _limit_concept_score(row: pd.Series) -> float:
        rank = _safe_float(row.get("rank"))
        up_nums = _safe_float(row.get("up_nums"))
        cons_nums = _safe_float(row.get("cons_nums"))
        pct_chg = abs(_safe_float(row.get("pct_chg")))
        rank_bonus = max((25.0 - rank) / 25.0, 0.0) if rank > 0 else 0.0
        return (
            1.0
            + min(up_nums / 60.0, 0.6)
            + min(cons_nums / 40.0, 0.4)
            + min(pct_chg / 10.0, 0.3)
            + rank_bonus * 0.3
        )

    def _record_hot_stock(self, row: pd.Series) -> None:
        code = _normalize_ts_code(str(row.get("ts_code", "")))
        if not code:
            return
        name = str(row.get("name", "")).strip()
        if name:
            self._name_by_code[code] = name

        source = str(row.get("event_source", "") or "hot_event")
        score = self._event_row_score(row)
        self._stock_hot_score[code] = max(self._stock_hot_score.get(code, 1.0), 1.0 + score)
        self._hot_seed_scores[code] = max(self._hot_seed_scores.get(code, 0.0), score)
        self._hot_seed_sources.setdefault(code, set()).add(source)

        concepts: list[str] = []
        for column in ("theme", "lu_desc", "tag"):
            concepts.extend(_split_concept_text(row.get(column)))
        for concept in concepts:
            if not concept:
                continue
            canonical = canonicalize_concept(concept) or concept
            self._event_stock_concepts.setdefault(code, set()).add(canonical)
            self._add_event_codes(concept, code)

    def _record_limit_concept(self, row: pd.Series) -> None:
        name = str(row.get("name", "")).strip()
        if not name or "ST" in name.upper():
            return
        self._record_concept_strength(name, self._limit_concept_score(row))

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

    @staticmethod
    def _fuzzy_lookup_matches(
        lookup: dict[str, set[str]],
        term_lowers: list[str],
    ) -> set[str]:
        results: set[str] = set()
        for name, codes in lookup.items():
            name_lower = name.lower()
            if any(term in name_lower or name_lower in term for term in term_lowers):
                results |= codes
        return results

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
        results |= self._fuzzy_lookup_matches(self._dc_name_lookup, term_lowers)

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
        results |= self._fuzzy_lookup_matches(self._kpl_lookup, term_lowers)

        # 5. Try same-day event stocks from limit-up/hot-list rows.
        for term in terms:
            if term in self._event_lookup:
                results |= self._event_lookup[term]
        results |= self._fuzzy_lookup_matches(self._event_lookup, term_lowers)

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

        # 2. Sort by relevance and limit. The free-form topic label is display-only;
        # only related concepts that match deterministic concept lookups may map stocks.
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

    def hotspot_seed_candidates(self, max_stocks: int = 50) -> list[dict[str, Any]]:
        if max_stocks <= 0 or not self._hot_seed_scores:
            return []
        sorted_codes = sorted(self._hot_seed_scores.items(), key=lambda item: -float(item[1]))
        max_score = max((float(score) for _, score in sorted_codes), default=1.0)
        result: list[dict[str, Any]] = []
        for code, score in sorted_codes[:max_stocks]:
            result.append(
                {
                    "ts_code": code,
                    "name": self._name_by_code.get(code, ""),
                    "score": round(float(score), 4),
                    "relevance": round(min(float(score) / max_score, 1.0), 3),
                    "source_topics": ["今日涨停热度"],
                    "source_concepts": sorted(self._event_stock_concepts.get(code, set()))[:8],
                    "match_sources": sorted(self._hot_seed_sources.get(code, set())),
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

        def merge_stock(stock: dict[str, Any]) -> None:
            code = stock["ts_code"]
            source_topic = stock.get("source_topic")
            source_topics = list(stock.get("source_topics", []))
            if source_topic and source_topic not in source_topics:
                source_topics.append(source_topic)
            source_concepts = list(stock.get("source_concepts", []))
            if code in seen:
                seen[code]["score"] += stock.get("score", stock["relevance"])
                if stock.get("name") and not seen[code].get("name"):
                    seen[code]["name"] = stock["name"]
                for topic_name in source_topics:
                    if topic_name not in seen[code]["source_topics"]:
                        seen[code]["source_topics"].append(topic_name)
                for concept in source_concepts:
                    if concept not in seen[code]["source_concepts"]:
                        seen[code]["source_concepts"].append(concept)
            else:
                seen[code] = {
                    "ts_code": code,
                    "name": stock.get("name", ""),
                    "score": stock.get("score", stock["relevance"]),
                    "relevance": stock["relevance"],
                    "source_topics": source_topics,
                    "source_concepts": source_concepts,
                }

        for topic in topics:
            stocks = self.map_topic_to_stocks(topic, max_stocks=max_stocks_per_topic)
            for stock in stocks:
                merge_stock(stock)

        for stock in self.hotspot_seed_candidates(max_total - len(seen)):
            merge_stock(stock)

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
