from __future__ import annotations

from typing import Any

from .stock_mapper import _safe_float


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.55:
        return "medium"
    return "watch"


def apply_candidate_confidence(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add a stable confidence score built from explainable candidate evidence."""
    if not stocks:
        return []

    out: list[dict[str, Any]] = []
    for stock in stocks:
        item = dict(stock)
        topics = _as_list(item.get("source_topics"))
        concepts = _as_list(item.get("source_concepts"))
        relevance = _clip(_safe_float(item.get("relevance"), _safe_float(item.get("score"))))
        source_breadth = _clip((len(topics) + min(len(concepts), 4) * 0.5) / 4.0)
        daily_score = _safe_float(item.get("daily_confirm_score"), 0.5)
        hotspot_score = _safe_float(item.get("hotspot_feature_score"), 0.5)
        liquidity_score = _safe_float(item.get("liquidity_score"), 0.5)

        confidence = _clip(
            0.35 * relevance
            + 0.20 * source_breadth
            + 0.20 * daily_score
            + 0.15 * hotspot_score
            + 0.10 * liquidity_score
        )
        confidence_multiplier = 0.75 + 0.25 * confidence
        base_score = _safe_float(item.get("score"), relevance)
        item["confidence_score"] = round(confidence, 4)
        item["confidence_label"] = _label(confidence)
        item["confidence_components"] = {
            "relevance": round(relevance, 4),
            "source_breadth": round(source_breadth, 4),
            "daily_confirm": round(daily_score, 4),
            "hotspot_feature": round(hotspot_score, 4),
            "liquidity": round(liquidity_score, 4),
        }
        item["pre_confidence_score"] = round(base_score, 6)
        item["score"] = round(base_score * confidence_multiplier, 6)
        out.append(item)

    max_score = max((_safe_float(item.get("score")) for item in out), default=0.0)
    if max_score > 0:
        for item in out:
            item["relevance"] = round(min(_safe_float(item.get("score")) / max_score, 1.0), 3)
    return sorted(out, key=lambda item: (-_safe_float(item.get("score")), str(item.get("ts_code"))))
