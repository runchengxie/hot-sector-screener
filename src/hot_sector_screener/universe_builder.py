from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .confidence import apply_candidate_confidence
from .daily_confirmation import apply_daily_confirmation_overlay, load_daily_history
from .data_sources.platform import (
    list_available_dates,
    load_daily_data,
    load_dc_concept,
    load_dc_concept_cons,
    load_hotspot_features,
    load_kpl_concept_cons,
    load_ths_hot,
)
from .data_sources.rotation_signal import load_industry_signal
from .outcome_evaluation import build_candidate_outcome_report
from .paths import ensure_output_dir
from .quality_report import build_candidate_quality_report
from .ranking import apply_hotspot_feature_overlay
from .signal_export import write_signal_artifacts
from .stock_mapper import StockMapper, apply_liquidity_filter
from .topic_classifier import TopicClassifier, build_topic_prompt


def _fmt_date(d: str | None) -> str:
    if d:
        return d.replace("-", "")
    return date.today().isoformat().replace("-", "")


def _df_to_dicts(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return df.fillna("").to_dict(orient="records")


def _load_optional_daily(date_str: str) -> pd.DataFrame:
    try:
        return load_daily_data(date_str)
    except RuntimeError:
        return pd.DataFrame()


def _future_daily_frames(
    date_int: str,
    horizons: tuple[int, ...] = (1, 3, 5),
) -> dict[int, pd.DataFrame]:
    try:
        dates = list_available_dates("daily")
    except RuntimeError:
        return {}
    future_dates = [d for d in dates if d > date_int]
    frames: dict[int, pd.DataFrame] = {}
    for horizon in horizons:
        if len(future_dates) >= horizon:
            frames[horizon] = _load_optional_daily(future_dates[horizon - 1])
    return frames


def _future_daily_sequence(date_int: str, max_horizon: int = 5) -> list[pd.DataFrame]:
    try:
        dates = list_available_dates("daily")
    except RuntimeError:
        return []
    future_dates = [d for d in dates if d > date_int]
    return [_load_optional_daily(day) for day in future_dates[:max_horizon]]


class Screener:
    """Main builder: collect data → classify topics → map stocks → output universe."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        uc = self.config.get("universe", {})
        self.max_candidates = uc.get("max_candidates", 100)
        self.min_candidates = uc.get("min_candidates", 30)
        self.topics_per_run = uc.get("topics_per_run", 5)
        self.stocks_per_topic = uc.get("stocks_per_topic", 25)
        self.min_daily_amount_rank_pct = uc.get("min_daily_amount_rank_pct", 80)
        self.max_price = uc.get("max_price", 200.0)
        self.min_price = uc.get("min_price", 2.0)
        self.max_st_allow = uc.get("max_st_allow", False)
        self.hotspot_feature_overlay = uc.get("hotspot_feature_overlay", True)
        self.hotspot_feature_weight = float(uc.get("hotspot_feature_weight", 0.25))
        self.daily_confirmation_enabled = bool(uc.get("daily_confirmation_enabled", True))
        self.daily_confirmation_weight = float(uc.get("daily_confirmation_weight", 0.20))
        self.daily_confirmation_lookback = int(uc.get("daily_confirmation_lookback", 20))
        self.min_daily_confirmation_score = uc.get("min_daily_confirmation_score")
        self.confidence_enabled = bool(uc.get("confidence_enabled", True))

        self.classifier = TopicClassifier(enabled=self.config.get("llm", {}).get("enabled", True))

    def scan(self, trade_date: str | None = None) -> dict[str, Any]:
        """Collect hotspot data without LLM classification.

        Returns raw data overview.
        """
        date_int = _fmt_date(trade_date)
        date_str = f"{date_int[:4]}-{date_int[4:6]}-{date_int[6:]}"

        ths = load_ths_hot(date_str)
        dc = load_dc_concept(date_str)
        dc_cons = load_dc_concept_cons(date_str)
        kpl_cons = load_kpl_concept_cons(date_str)
        hf = load_hotspot_features(date_str)
        ind_signal = load_industry_signal()
        daily = _load_optional_daily(date_str)
        daily_history = load_daily_history(
            date_int,
            lookback=self.daily_confirmation_lookback,
        )

        return {
            "date": date_str,
            "ths_hot": {
                "rows": len(ths),
                "columns": list(ths.columns) if not ths.empty else [],
                "sample": _df_to_dicts(ths.head(10)),
            },
            "dc_concept": {
                "rows": len(dc),
                "columns": list(dc.columns) if not dc.empty else [],
                "sample": _df_to_dicts(dc.head(10)),
            },
            "dc_concept_cons": {
                "rows": len(dc_cons),
                "columns": list(dc_cons.columns) if not dc_cons.empty else [],
            },
            "kpl_concept_cons": {
                "rows": len(kpl_cons),
                "columns": list(kpl_cons.columns) if not kpl_cons.empty else [],
            },
            "hotspot_features": {
                "rows": len(hf),
                "columns": list(hf.columns) if not hf.empty else [],
            },
            "daily": {
                "rows": len(daily),
                "columns": list(daily.columns) if not daily.empty else [],
            },
            "daily_history": {
                "rows": len(daily_history),
                "columns": list(daily_history.columns) if not daily_history.empty else [],
            },
            "industry_signal": {
                "available": len(ind_signal) > 0,
                "rows": len(ind_signal),
            },
        }

    def build_prompt(
        self,
        trade_date: str | None = None,
        stock_limit: int = 30,
        concept_limit: int = 20,
    ) -> dict[str, Any]:
        """Collect hotspot data and build the LLM prompt (no LLM call).

        Returns dict with prompt text and data summary.
        """
        date_int = _fmt_date(trade_date)
        date_str = f"{date_int[:4]}-{date_int[4:6]}-{date_int[6:]}"

        ths = load_ths_hot(date_str, limit=stock_limit)
        dc = load_dc_concept(date_str)
        ind_signal = load_industry_signal()

        ths_stocks = _df_to_dicts(ths)
        dc_list = _df_to_dicts(dc)
        ind_list = _df_to_dicts(ind_signal) if not ind_signal.empty else None

        prompt = build_topic_prompt(
            ths_hot_stocks=ths_stocks,
            dc_concepts=dc_list[:concept_limit],
            industry_signals=ind_list,
            latest_date=date_str,
        )

        return {
            "date": date_str,
            "date_int": date_int,
            "prompt": prompt,
            "prompt_length": len(prompt),
            "stock_count": len(ths_stocks),
            "concept_count": len(dc_list),
            "industry_signal_available": ind_list is not None,
        }

    def build_universe(
        self,
        trade_date: str | None = None,
        output_dir: str | None = None,
        topics: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run the full pipeline: collect → classify → map → output.

        If `topics` is provided, skips the LLM classification step entirely.
        """
        date_int = _fmt_date(trade_date)
        date_str = f"{date_int[:4]}-{date_int[4:6]}-{date_int[6:]}"

        # 1. Collect data
        ths = load_ths_hot(date_str)
        dc = load_dc_concept(date_str)
        dc_cons = load_dc_concept_cons(date_str)
        kpl_cons = load_kpl_concept_cons(date_str)
        hf = load_hotspot_features(date_str)
        ind_signal = load_industry_signal()
        daily = _load_optional_daily(date_str)
        daily_history = (
            load_daily_history(
                date_int,
                lookback=self.daily_confirmation_lookback,
            )
            if self.daily_confirmation_enabled
            else pd.DataFrame()
        )

        # 2. Classify topics (or use pre-classified)
        ths_stocks = _df_to_dicts(ths)
        dc_list = _df_to_dicts(dc)
        ind_list = _df_to_dicts(ind_signal) if not ind_signal.empty else None

        if topics is not None:
            # Use pre-classified topics loaded from file
            pass  # topics already set
        else:
            topics = self.classifier.classify(
                ths_hot_stocks=ths_stocks,
                dc_concepts=dc_list,
                industry_signals=ind_list,
                latest_date=date_str,
            )

        # 3. Map topics → stocks
        mapper = StockMapper(dc_cons, kpl_cons, dc_concept_df=dc)
        raw_stocks = mapper.map_topics(
            topics,
            max_stocks_per_topic=self.stocks_per_topic,
            max_total=self.max_candidates,
        )
        ranked_stocks = (
            apply_hotspot_feature_overlay(
                raw_stocks,
                hf,
                weight=self.hotspot_feature_weight,
            )
            if self.hotspot_feature_overlay
            else raw_stocks
        )
        confirmed_stocks = (
            apply_daily_confirmation_overlay(
                ranked_stocks,
                daily_history,
                weight=self.daily_confirmation_weight,
                min_score=self.min_daily_confirmation_score,
            )
            if self.daily_confirmation_enabled
            else ranked_stocks
        )

        # 4. Apply filters
        filtered = apply_liquidity_filter(
            confirmed_stocks,
            daily_df=daily,
            min_amount_rank_pct=self.min_daily_amount_rank_pct,
            max_price=self.max_price,
            min_price=self.min_price,
            allow_st=self.max_st_allow,
        )
        if self.confidence_enabled:
            filtered = apply_candidate_confidence(filtered)
        future_daily_sequence = _future_daily_sequence(date_int)
        future_daily_frames = {
            horizon: future_daily_sequence[horizon - 1]
            for horizon in (1, 3, 5)
            if len(future_daily_sequence) >= horizon
        }
        quality_report = build_candidate_quality_report(
            filtered,
            daily,
            future_daily_frames,
        )
        outcome_report = build_candidate_outcome_report(
            filtered,
            daily,
            future_daily_sequence,
            horizons=(1, 3),
        )

        # 5. Build output
        result = {
            "date": date_str,
            "date_int": date_int,
            "generated_at": datetime.now().isoformat(),
            "topics": topics,
            "candidate_universe": filtered,
            "universe_size": len(filtered),
            "config_snapshot": {
                "max_candidates": self.max_candidates,
                "min_candidates": self.min_candidates,
                "llm_enabled": self.classifier.enabled,
                "min_daily_amount_rank_pct": self.min_daily_amount_rank_pct,
                "max_price": self.max_price,
                "min_price": self.min_price,
                "max_st_allow": self.max_st_allow,
                "hotspot_feature_overlay": self.hotspot_feature_overlay,
                "hotspot_feature_weight": self.hotspot_feature_weight,
                "daily_confirmation_enabled": self.daily_confirmation_enabled,
                "daily_confirmation_weight": self.daily_confirmation_weight,
                "daily_confirmation_lookback": self.daily_confirmation_lookback,
                "min_daily_confirmation_score": self.min_daily_confirmation_score,
                "confidence_enabled": self.confidence_enabled,
            },
            "data_sources": {
                "ths_hot_available": len(ths) > 0,
                "dc_concept_available": len(dc) > 0,
                "dc_concept_cons_available": len(dc_cons) > 0,
                "kpl_concept_cons_available": len(kpl_cons) > 0,
                "hotspot_features_available": len(hf) > 0,
                "daily_available": len(daily) > 0,
                "daily_history_available": len(daily_history) > 0,
                "industry_signal_available": len(ind_signal) > 0,
            },
            "quality_report": quality_report,
            "outcome_report": outcome_report,
        }

        # 6. Write output
        out_dir = Path(output_dir) if output_dir else ensure_output_dir(date_int)
        out_dir.mkdir(parents=True, exist_ok=True)

        # JSON output
        json_path = out_dir / "candidate_universe.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        # CSV output (stocks only)
        csv_path = out_dir / "candidate_universe.csv"
        if filtered:
            csv_data = pd.DataFrame(filtered)
            csv_data.to_csv(csv_path, index=False)

        quality_path = out_dir / "candidate_quality.json"
        with open(quality_path, "w", encoding="utf-8") as f:
            json.dump(quality_report, f, ensure_ascii=False, indent=2, default=str)

        outcomes_path = out_dir / "candidate_outcomes.json"
        with open(outcomes_path, "w", encoding="utf-8") as f:
            json.dump(outcome_report, f, ensure_ascii=False, indent=2, default=str)

        # Run config
        config_path = out_dir / "run_config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(
                self.config,
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        result["output_dir"] = str(out_dir)
        signal_files: dict[str, str] = {}
        output_cfg = self.config.get("output", {})
        if output_cfg.get("export_signals", True):
            signal_files = write_signal_artifacts(
                result,
                out_dir,
                model_version=str(output_cfg.get("signal_model_version", "hotsector-theme-v2")),
                feature_set_id=str(
                    output_cfg.get("signal_feature_set_id", "topic-concept-hotspot-overlay")
                ),
                eligible_for_live=bool(output_cfg.get("eligible_for_live", True)),
            )

        # Lineage
        lineage = {
            "date": date_str,
            "generated_at": result["generated_at"],
            "run_config": config_path.name,
            "data_sources": dict(result["data_sources"]),
            "topics_count": len(topics),
            "universe_size": len(filtered),
            "output_files": {
                "json": str(json_path),
                "csv": str(csv_path) if filtered else None,
                "quality": str(quality_path),
                "outcomes": str(outcomes_path),
                "signals": signal_files or None,
            },
        }
        lineage_path = out_dir / "lineage.json"
        with open(lineage_path, "w", encoding="utf-8") as f:
            json.dump(lineage, f, ensure_ascii=False, indent=2)

        if signal_files:
            result["signal_artifacts"] = signal_files
        return result
