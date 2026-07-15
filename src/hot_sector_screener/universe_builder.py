from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .candidate_contract import (
    CANDIDATE_ARTIFACT_TYPE,
    CANDIDATE_MARKET,
    CANDIDATE_SCHEMA_VERSION,
    validate_candidate_result,
)
from .confidence import apply_candidate_confidence
from .config import normalize_llm_config
from .daily_confirmation import apply_daily_confirmation_overlay, load_daily_history
from .data_sources.platform import (
    load_daily_data,
    load_dc_concept,
    load_dc_concept_cons,
    load_hotspot_features,
    load_kpl_concept_cons,
    load_kpl_list,
    load_limit_cpt_list,
    load_limit_list_ths,
    load_limit_step,
    load_ths_hot,
)
from .data_sources.rotation_signal import load_industry_signal
from .observation_time import MARKET_TIMEZONE_NAME, resolve_observation_date, shanghai_now
from .paths import ensure_output_dir
from .ranking import apply_hotspot_feature_overlay
from .signal_export import write_signal_artifacts
from .stock_mapper import StockMapper, apply_liquidity_filter
from .topic_classifier import (
    TopicClassificationError,
    TopicClassifier,
    build_topic_prompt,
    validate_and_sanitize_topics,
)
from .topic_provider import TopicProvider


def _df_to_dicts(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return df.fillna("").to_dict(orient="records")


def _load_optional_daily(date_str: str) -> pd.DataFrame:
    try:
        return load_daily_data(date_str)
    except RuntimeError:
        return pd.DataFrame()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _with_event_source(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["event_source"] = source
    return out


def _event_stock_frame(kpl_list: pd.DataFrame, limit_list_ths: pd.DataFrame) -> pd.DataFrame:
    frames = [
        _with_event_source(kpl_list, "kpl_list"),
        _with_event_source(limit_list_ths, "limit_list_ths"),
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _limit_cpt_topic_records(limit_cpt: pd.DataFrame) -> list[dict[str, Any]]:
    if limit_cpt.empty:
        return []
    records: list[dict[str, Any]] = []
    for _, row in limit_cpt.iterrows():
        name = str(row.get("name", "")).strip()
        if not name or "ST" in name.upper():
            continue
        rank = _safe_float(row.get("rank"), 999.0)
        up_nums = _safe_float(row.get("up_nums"))
        cons_nums = _safe_float(row.get("cons_nums"))
        pct_chg = _safe_float(row.get("pct_chg"))
        rank_strength = max(1000.0 - rank * 25.0, 100.0) if rank > 0 else 100.0
        records.append(
            {
                "name": name,
                "hot": max(1.0, 10000.0 - rank),
                "strength": max(
                    up_nums * 20.0,
                    cons_nums * 20.0,
                    abs(pct_chg) * 100.0,
                    rank_strength,
                ),
                "pct_change": pct_chg,
                "z_t_num": up_nums,
                "lead_stock": "",
                "source_signal": "limit_cpt_list",
            }
        )
    return records


def _industry_signal_date(frame: pd.DataFrame) -> str | None:
    if frame.empty:
        return None
    from_attrs = frame.attrs.get("signal_date")
    if from_attrs:
        return str(from_attrs).replace("-", "")[:8]
    if "signal_date" not in frame.columns:
        return None
    dates = frame["signal_date"].dropna().astype(str).str.replace("-", "", regex=False)
    return max((value[:8] for value in dates if len(value) >= 8), default=None)


def _rotation_provenance(frame: pd.DataFrame, as_of_date: str) -> dict[str, Any]:
    return {
        "as_of_date": as_of_date,
        "signal_date": _industry_signal_date(frame),
        "provenance_level": str(frame.attrs.get("provenance_level") or "unavailable"),
        "strict_point_in_time": False,
        "publisher_receipt_verified": False,
        "source_path": frame.attrs.get("source_path"),
        "limitation": "publisher receipt with published_at/data_cutoff/hash is unavailable",
    }


def _deferred_evaluation_report() -> dict[str, Any]:
    return {
        "available": False,
        "reason": "future_data_excluded_from_generation",
        "horizons": {},
    }


def _contract_evidence(date_int: str, ind_signal: pd.DataFrame) -> dict[str, Any]:
    generated_at = shanghai_now()
    same_day_generation = generated_at.strftime("%Y%m%d") == date_int
    temporal_context = (
        "same_day_eod_generation" if same_day_generation else "post_observation_generation"
    )
    limitations = [
        "rotation_publisher_receipt_unavailable",
        "candidate_artifact_does_not_establish_out_of_sample_validity",
    ]
    if not same_day_generation:
        limitations.append("post_observation_reconstruction_not_oos")
    return {
        "generated_at": generated_at.isoformat(),
        "provenance": {
            "timezone": MARKET_TIMEZONE_NAME,
            "observation_date": date_int,
            "data_cutoff": date_int,
            "future_data_included": False,
            "artifact_role": "candidate_universe",
            "strict_point_in_time": False,
            "rotation": _rotation_provenance(ind_signal, date_int),
        },
        "evidence": {
            "strict_point_in_time": False,
            "out_of_sample_claim": False,
            "temporal_context": temporal_context,
            "limitations": limitations,
        },
    }


def _data_source_status(frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
    status: dict[str, Any] = {
        f"{source}_available": not frame.empty for source, frame in frames.items()
    }
    industry_signal = frames["industry_signal"]
    status["industry_signal_date"] = _industry_signal_date(industry_signal)
    return status


class Screener:
    """Main builder: collect data → classify topics → map stocks → output universe."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        topic_provider: TopicProvider | None = None,
    ):
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
        self.rotation_signal_dir = self.config.get("rotation_signal_dir")

        llm_config = normalize_llm_config(self.config.get("llm"))
        self.classifier = TopicClassifier(
            enabled=llm_config["enabled"],
            provider=topic_provider,
        )

    def _config_snapshot(self) -> dict[str, Any]:
        return {
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
        }

    def scan(self, trade_date: str | None = None) -> dict[str, Any]:
        """Collect hotspot data without LLM classification.

        Returns raw data overview.
        """
        date_int = resolve_observation_date(trade_date)
        date_str = f"{date_int[:4]}-{date_int[4:6]}-{date_int[6:]}"

        ths = load_ths_hot(date_str)
        dc = load_dc_concept(date_str)
        dc_cons = load_dc_concept_cons(date_str)
        kpl_cons = load_kpl_concept_cons(date_str)
        kpl_list = load_kpl_list(date_str)
        limit_step = load_limit_step(date_str)
        limit_cpt = load_limit_cpt_list(date_str)
        limit_list_ths = load_limit_list_ths(date_str)
        hf = load_hotspot_features(date_str)
        ind_signal = load_industry_signal(
            as_of_date=date_int,
            run_dir=self.rotation_signal_dir,
        )
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
            "kpl_list": {
                "rows": len(kpl_list),
                "columns": list(kpl_list.columns) if not kpl_list.empty else [],
            },
            "limit_step": {
                "rows": len(limit_step),
                "columns": list(limit_step.columns) if not limit_step.empty else [],
            },
            "limit_cpt_list": {
                "rows": len(limit_cpt),
                "columns": list(limit_cpt.columns) if not limit_cpt.empty else [],
            },
            "limit_list_ths": {
                "rows": len(limit_list_ths),
                "columns": list(limit_list_ths.columns) if not limit_list_ths.empty else [],
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
        date_int = resolve_observation_date(trade_date)
        date_str = f"{date_int[:4]}-{date_int[4:6]}-{date_int[6:]}"

        ths = load_ths_hot(date_str, limit=stock_limit)
        dc = load_dc_concept(date_str)
        ind_signal = load_industry_signal(
            as_of_date=date_int,
            run_dir=self.rotation_signal_dir,
        )

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
        topics: object | None = None,
    ) -> dict[str, Any]:
        """Run the full pipeline: collect → classify → map → output.

        If `topics` is provided, skips the LLM classification step entirely.
        """
        date_int = resolve_observation_date(trade_date)
        date_str = f"{date_int[:4]}-{date_int[4:6]}-{date_int[6:]}"

        # 1. Collect data
        ths = load_ths_hot(date_str)
        dc = load_dc_concept(date_str)
        dc_cons = load_dc_concept_cons(date_str)
        kpl_cons = load_kpl_concept_cons(date_str)
        kpl_list = load_kpl_list(date_str)
        limit_step = load_limit_step(date_str)
        limit_cpt = load_limit_cpt_list(date_str)
        limit_list_ths = load_limit_list_ths(date_str)
        hf = load_hotspot_features(date_str)
        ind_signal = load_industry_signal(
            as_of_date=date_int,
            run_dir=self.rotation_signal_dir,
        )
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
        classifier_concepts = dc_list + _limit_cpt_topic_records(limit_cpt)
        ind_list = _df_to_dicts(ind_signal) if not ind_signal.empty else None

        if topics is not None:
            topics = validate_and_sanitize_topics(
                topics,
                ths_hot_stocks=ths_stocks,
                dc_concepts=classifier_concepts,
                industry_signals=ind_list,
            )
            topic_classification_lineage: dict[str, Any] = {"mode": "external_topics"}
        else:
            topics = self.classifier.classify(
                ths_hot_stocks=ths_stocks,
                dc_concepts=classifier_concepts,
                industry_signals=ind_list,
                latest_date=date_str,
            )
            receipt = self.classifier.last_provider_receipt
            if not self.classifier.enabled:
                topic_classification_lineage = {
                    "mode": "deterministic",
                    "reason": "explicitly_disabled",
                }
            elif receipt is None:
                raise TopicClassificationError(
                    "remote topic classification completed without an audit receipt"
                )
            else:
                topic_classification_lineage = {
                    "mode": "remote",
                    "provider_receipt": receipt.to_lineage(),
                }

        # 3. Map topics → stocks
        mapper = StockMapper(
            dc_cons,
            kpl_cons,
            dc_concept_df=dc,
            hot_stocks_df=_event_stock_frame(kpl_list, limit_list_ths),
            limit_step_df=limit_step,
            limit_cpt_df=limit_cpt,
        )
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
        quality_report = _deferred_evaluation_report()
        outcome_report = _deferred_evaluation_report()

        # 5. Build output
        result = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "artifact_type": CANDIDATE_ARTIFACT_TYPE,
            "market": CANDIDATE_MARKET,
            "date": date_str,
            "date_int": date_int,
            "observation_date": date_int,
            "data_cutoff": date_int,
            "data_cutoff_semantics": "end_of_day",
            "execution_not_before": "next_trading_session",
            "future_data_included": False,
            **_contract_evidence(date_int, ind_signal),
            "topics": topics,
            "candidate_universe": filtered,
            "universe_size": len(filtered),
            "config_snapshot": self._config_snapshot(),
            "data_sources": _data_source_status(
                {
                    "ths_hot": ths,
                    "dc_concept": dc,
                    "dc_concept_cons": dc_cons,
                    "kpl_concept_cons": kpl_cons,
                    "kpl_list": kpl_list,
                    "limit_step": limit_step,
                    "limit_cpt_list": limit_cpt,
                    "limit_list_ths": limit_list_ths,
                    "hotspot_features": hf,
                    "daily": daily,
                    "daily_history": daily_history,
                    "industry_signal": ind_signal,
                }
            ),
            "quality_report": quality_report,
            "outcome_report": outcome_report,
        }
        result = validate_candidate_result(result)

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
            )

        # Lineage
        lineage = {
            "schema_version": result["schema_version"],
            "artifact_type": result["artifact_type"],
            "market": result["market"],
            "date": date_str,
            "observation_date": date_int,
            "data_cutoff": date_int,
            "data_cutoff_semantics": "end_of_day",
            "execution_not_before": "next_trading_session",
            "future_data_included": False,
            "generated_at": result["generated_at"],
            "provenance": result["provenance"],
            "evidence": result["evidence"],
            "run_config": config_path.name,
            "data_sources": dict(result["data_sources"]),
            "topic_classification": topic_classification_lineage,
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
