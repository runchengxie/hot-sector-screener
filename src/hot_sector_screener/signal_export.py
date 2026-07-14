from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

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
    raw = str(result.get("date_int") or result.get("date") or "").replace("-", "")
    if len(raw) >= 8:
        return raw[:8]
    return datetime.now().strftime("%Y%m%d")


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
    eligible_for_live: bool = True,
) -> pd.DataFrame:
    """Convert a candidate universe result into the canonical alpha-research schema."""
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
                "eligible_for_live": bool(eligible_for_live),
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
    return {
        "contract": SIGNAL_CONTRACT_NAME,
        "schema_version": SIGNAL_SCHEMA_VERSION,
        "file": str(parquet_path),
        "csv_file": str(csv_path),
        "rows": len(frame),
        "signal_date": _date_int(result),
        "required_columns": list(SIGNAL_COLUMNS),
        "score_columns": ["raw_pred", "signal_eval", "signal_backtest"],
        "rank_col": "rank",
        "date_col": "signal_date",
        "symbol_col": "symbol",
        "source": {
            "date": result.get("date"),
            "generated_at": result.get("generated_at"),
            "universe_size": result.get("universe_size"),
            "data_sources": result.get("data_sources", {}),
            "config_snapshot": result.get("config_snapshot", {}),
        },
    }


def write_signal_artifacts(
    result: dict[str, Any],
    output_dir: str | Path,
    *,
    model_version: str = "hotsector-theme-v2",
    feature_set_id: str = "topic-concept-hotspot-overlay",
    eligible_for_live: bool = True,
) -> dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    frame = build_signal_frame(
        result,
        model_version=model_version,
        feature_set_id=feature_set_id,
        eligible_for_live=eligible_for_live,
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
    return json.loads(resolved.read_text(encoding="utf-8"))
