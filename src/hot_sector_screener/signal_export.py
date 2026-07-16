from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .candidate_contract import CandidateContractError, validate_candidate_result
from .observation_time import date_key

SIGNAL_SCHEMA_VERSION = 1
SIGNAL_CONTRACT_NAME = "alpha_research.signals"
SIGNAL_FILE_NAME = "signals.parquet"
SIGNAL_META_FILE_NAME = "signals.meta.json"
SIGNAL_CSV_FILE_NAME = "signals.csv"
SIGNAL_COLUMNS = (
    "signal_date",
    "symbol",
    "raw_pred",
    "signal_eval",
    "signal_backtest",
    "signal_direction",
    "rank",
    "model_version",
    "feature_set_id",
    "eligible_for_backtest",
    "eligible_for_live",
)


def _date_int(result: dict[str, Any]) -> str:
    return date_key(result.get("observation_date"))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_signal_frame(
    result: dict[str, Any],
    *,
    model_version: str = "hotsector-theme-v2",
    feature_set_id: str = "topic-concept-hotspot-overlay",
) -> pd.DataFrame:
    """Convert a candidate universe result into the canonical alpha-research schema."""
    result = validate_candidate_result(result)
    candidates = list(result.get("candidate_universe") or [])
    if not candidates:
        return pd.DataFrame(columns=list(SIGNAL_COLUMNS))

    signal_date = _date_int(result)
    rows: list[dict[str, Any]] = []
    for item in candidates:
        symbol = str(item.get("ts_code") or item.get("symbol") or "").strip()
        if not symbol:
            continue
        signal_score = _as_float(item.get("relevance"), _as_float(item.get("score"), 0.0))
        raw_pred = _as_float(item.get("score"), signal_score)
        rows.append(
            {
                "signal_date": signal_date,
                "symbol": symbol,
                "raw_pred": raw_pred,
                "signal_eval": signal_score,
                "signal_backtest": signal_score,
                "signal_direction": 1.0,
                "model_version": model_version,
                "feature_set_id": feature_set_id,
                "eligible_for_backtest": True,
                "eligible_for_live": False,
                "name": item.get("name", ""),
                "source_topics": item.get("source_topics", []),
                "source_concepts": item.get("source_concepts", []),
                "liquidity_score": item.get("liquidity_score"),
                "amount_rank_pct": item.get("amount_rank_pct"),
                "close": item.get("close"),
                "hotspot_feature_score": item.get("hotspot_feature_score"),
                "hotspot_score_multiplier": item.get("hotspot_score_multiplier"),
                "daily_confirm_score": item.get("daily_confirm_score"),
                "trend_score": item.get("trend_score"),
                "volume_score": item.get("volume_score"),
                "risk_score": item.get("risk_score"),
                "ret_5d": item.get("ret_5d"),
                "ret_10d": item.get("ret_10d"),
                "close_to_20d_high": item.get("close_to_20d_high"),
                "amount_ratio_20d": item.get("amount_ratio_20d"),
                "confidence_score": item.get("confidence_score"),
                "confidence_label": item.get("confidence_label"),
            }
        )

    if not rows:
        return pd.DataFrame(columns=list(SIGNAL_COLUMNS))

    frame = pd.DataFrame(rows)
    frame = frame.sort_values(
        ["signal_date", "signal_backtest", "raw_pred", "symbol"],
        ascending=[True, False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    frame["rank"] = frame.groupby("signal_date", sort=False).cumcount() + 1
    for column in ("raw_pred", "signal_eval", "signal_backtest", "signal_direction"):
        frame[column] = pd.Series(
            pd.to_numeric(frame[column], errors="coerce"),
            index=frame.index,
        ).fillna(0.0)
    frame["rank"] = frame["rank"].astype("int64")
    frame["eligible_for_backtest"] = frame["eligible_for_backtest"].astype(bool)
    frame["eligible_for_live"] = frame["eligible_for_live"].astype(bool)
    extra_columns = [column for column in frame.columns if column not in SIGNAL_COLUMNS]
    return frame.loc[:, list(SIGNAL_COLUMNS) + extra_columns].copy()


def signal_metadata(
    frame: pd.DataFrame,
    result: dict[str, Any],
    *,
    parquet_path: Path,
    csv_path: Path,
) -> dict[str, Any]:
    result = validate_candidate_result(result)
    provenance = result["provenance"]
    evidence = result["evidence"]
    return {
        "contract": SIGNAL_CONTRACT_NAME,
        "schema_version": SIGNAL_SCHEMA_VERSION,
        "file": str(parquet_path),
        "csv_file": str(csv_path),
        "rows": len(frame),
        "signal_date": _date_int(result),
        "data_cutoff": str(result.get("data_cutoff") or _date_int(result)),
        "data_cutoff_semantics": "end_of_day",
        "execution_not_before": "next_trading_session",
        "future_data_included": result["future_data_included"],
        "strict_point_in_time": evidence["strict_point_in_time"],
        "evidence": evidence,
        "artifact_role": "candidate_universe",
        "execution_eligible": False,
        "required_columns": list(SIGNAL_COLUMNS),
        "score_columns": ["raw_pred", "signal_eval", "signal_backtest"],
        "rank_col": "rank",
        "date_col": "signal_date",
        "symbol_col": "symbol",
        "source": {
            "date": result.get("date"),
            "generated_at": result.get("generated_at"),
            "universe_size": result.get("universe_size"),
            "source_mode": result.get("source_mode"),
            "fallback_reason": result.get("fallback_reason"),
            "source_gate": result.get("source_gate"),
            "data_sources": result.get("data_sources", {}),
            "config_snapshot": result.get("config_snapshot", {}),
            "candidate_schema_version": result["schema_version"],
            "candidate_artifact_type": result["artifact_type"],
            "candidate_provenance": provenance,
            "candidate_evidence": evidence,
        },
    }


def write_signal_artifacts(
    result: dict[str, Any],
    output_dir: str | Path,
    *,
    model_version: str = "hotsector-theme-v2",
    feature_set_id: str = "topic-concept-hotspot-overlay",
) -> dict[str, str]:
    result = validate_candidate_result(result)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    frame = build_signal_frame(
        result,
        model_version=model_version,
        feature_set_id=feature_set_id,
    )

    parquet_path = output_path / SIGNAL_FILE_NAME
    csv_path = output_path / SIGNAL_CSV_FILE_NAME
    meta_path = output_path / SIGNAL_META_FILE_NAME

    frame.to_parquet(parquet_path, index=False)
    frame.to_csv(csv_path, index=False)
    meta = signal_metadata(frame, result, parquet_path=parquet_path, csv_path=csv_path)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "parquet": str(parquet_path),
        "csv": str(csv_path),
        "metadata": str(meta_path),
    }


def load_candidate_result(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CandidateContractError(f"invalid candidate JSON: {exc}") from exc
    return validate_candidate_result(payload)
