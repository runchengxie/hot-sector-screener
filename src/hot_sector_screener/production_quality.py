from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_REQUIRED_SOURCES = (
    # Level-1 candidate signals require concept constituents plus same-day
    # limit-up/hotspot event sources. ths_hot is useful when fresh, but it can
    # lag while these sources already have the target trade date.
    "dc_concept",
    "dc_concept_cons",
    "kpl_concept_cons",
    "kpl_list",
    "limit_step",
    "limit_cpt_list",
    "limit_list_ths",
)


def parse_source_list(value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_REQUIRED_SOURCES
    parts = value.replace(";", ",").split(",") if isinstance(value, str) else list(value)
    return tuple(part.strip() for part in parts if str(part).strip())


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _candidate_count(payload: dict[str, Any]) -> int:
    universe = payload.get("candidate_universe")
    if isinstance(universe, list):
        return len(universe)
    return _as_int(payload.get("universe_size"), 0)


def validate_candidate_output(
    output_dir: str | Path,
    *,
    required_sources: tuple[str, ...] = DEFAULT_REQUIRED_SOURCES,
    min_candidates: int | None = None,
    require_signals: bool = True,
) -> list[str]:
    """Return production-readiness issues for one hotsector output directory."""
    out_dir = Path(output_dir)
    issues: list[str] = []

    candidate_path = out_dir / "candidate_universe.json"
    if not candidate_path.is_file():
        return [f"missing candidate_universe.json: {candidate_path}"]

    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"invalid candidate_universe.json: {exc}"]

    data_sources = payload.get("data_sources")
    data_sources = data_sources if isinstance(data_sources, dict) else {}
    for source in required_sources:
        key = f"{source}_available"
        if data_sources.get(key) is not True:
            issues.append(f"required source unavailable: {source}")

    count = _candidate_count(payload)
    configured_min = _as_int(
        (payload.get("config_snapshot") or {}).get("min_candidates"),
        0,
    )
    threshold = configured_min if min_candidates is None else int(min_candidates)
    if threshold > 0 and count < threshold:
        issues.append(f"candidate count {count} is below min_candidates {threshold}")

    if require_signals:
        signals_path = out_dir / "signals.parquet"
        if not signals_path.is_file():
            issues.append(f"missing signals.parquet: {signals_path}")
        else:
            try:
                signals = pd.read_parquet(signals_path)
            except Exception as exc:
                issues.append(f"unreadable signals.parquet: {exc}")
            else:
                if signals.empty:
                    issues.append("signals.parquet is empty")

        meta_path = out_dir / "signals.meta.json"
        if not meta_path.is_file():
            issues.append(f"missing signals.meta.json: {meta_path}")

    return issues
