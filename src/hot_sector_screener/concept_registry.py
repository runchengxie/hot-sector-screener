from __future__ import annotations

import re

_CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "AI芯片": ("AI芯片", "算力芯片", "GPU概念", "GPU", "英伟达概念"),
    "CPO概念": ("CPO概念", "CPO", "光模块", "光通信", "高速光模块"),
    "半导体设备": ("半导体设备", "国产半导体设备", "设备国产化", "芯片设备"),
    "先进封装": ("先进封装", "Chiplet", "HBM", "CoWoS", "封装测试"),
    "存储芯片": ("存储芯片", "存储", "DRAM", "NAND", "HBM存储"),
    "光刻胶": ("光刻胶", "半导体材料", "电子化学品", "材料国产替代"),
    "AI应用": ("AI应用", "大模型", "多模态AI", "人工智能"),
    "云计算": ("云计算", "数据中心", "服务器", "算力租赁"),
}


def _token(value: str) -> str:
    return re.sub(r"[\s_\-（）()·/\\]+", "", str(value).strip().lower())


def canonicalize_concept(name: object) -> str:
    """Return a stable concept label for fuzzy market theme names."""
    text = str(name or "").strip()
    if not text:
        return ""
    normalized = _token(text)
    for canonical, aliases in _CANONICAL_ALIASES.items():
        for alias in aliases:
            alias_token = _token(alias)
            if normalized == alias_token or alias_token in normalized or normalized in alias_token:
                return canonical
    return text


def expand_concept_terms(name: object) -> list[str]:
    """Return original, canonical, and known aliases for lookup matching."""
    text = str(name or "").strip()
    if not text:
        return []
    canonical = canonicalize_concept(text)
    terms = [text, canonical]
    terms.extend(_CANONICAL_ALIASES.get(canonical, ()))

    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        key = _token(term)
        if key and key not in seen:
            seen.add(key)
            result.append(term)
    return result
