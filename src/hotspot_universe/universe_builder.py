from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .config import load_config
from .data_sources.platform import (
    load_ths_hot,
    load_dc_concept,
    load_dc_concept_cons,
    load_kpl_concept_cons,
    load_hotspot_features,
)
from .data_sources.rotation_signal import load_industry_signal
from .paths import ensure_output_dir
from .stock_mapper import StockMapper, apply_liquidity_filter
from .topic_classifier import TopicClassifier


def _fmt_date(d: str | None) -> str:
    if d:
        return d.replace("-", "")
    return date.today().isoformat().replace("-", "")


def _df_to_dicts(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return df.fillna("").to_dict(orient="records")


class HotspotUniverseBuilder:
    """Main builder: collect data → classify topics → map stocks → output universe."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        uc = self.config.get("universe", {})
        self.max_candidates = uc.get("max_candidates", 100)
        self.min_candidates = uc.get("min_candidates", 30)
        self.topics_per_run = uc.get("topics_per_run", 5)
        self.stocks_per_topic = uc.get("stocks_per_topic", 25)

        self.classifier = TopicClassifier(
            enabled=self.config.get("llm", {}).get("enabled", True)
        )

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
            "industry_signal": {
                "available": len(ind_signal) > 0,
                "rows": len(ind_signal),
            },
        }

    def build_universe(
        self,
        trade_date: str | None = None,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Run the full pipeline: collect → classify → map → output."""
        date_int = _fmt_date(trade_date)
        date_str = f"{date_int[:4]}-{date_int[4:6]}-{date_int[6:]}"

        # 1. Collect data
        ths = load_ths_hot(date_str)
        dc = load_dc_concept(date_str)
        dc_cons = load_dc_concept_cons(date_str)
        kpl_cons = load_kpl_concept_cons(date_str)
        ind_signal = load_industry_signal()

        # 2. Classify topics
        ths_stocks = _df_to_dicts(ths)
        dc_list = _df_to_dicts(dc)
        ind_list = _df_to_dicts(ind_signal) if not ind_signal.empty else None

        topics = self.classifier.classify(
            ths_hot_stocks=ths_stocks,
            dc_concepts=dc_list,
            industry_signals=ind_list,
            latest_date=date_str,
        )

        # 3. Map topics → stocks
        mapper = StockMapper(dc_cons, kpl_cons)
        raw_stocks = mapper.map_topics(
            topics,
            max_stocks_per_topic=self.stocks_per_topic,
            max_total=self.max_candidates,
        )

        # 4. Apply filters
        filtered = apply_liquidity_filter(raw_stocks)

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
            },
            "data_sources": {
                "ths_hot_available": len(ths) > 0,
                "dc_concept_available": len(dc) > 0,
                "dc_concept_cons_available": len(dc_cons) > 0,
                "kpl_concept_cons_available": len(kpl_cons) > 0,
                "industry_signal_available": len(ind_signal) > 0,
            },
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

        # Lineage
        lineage = {
            "date": date_str,
            "generated_at": result["generated_at"],
            "run_config": config_path.name,
            "data_sources": {k: v for k, v in result["data_sources"].items()},
            "topics_count": len(topics),
            "universe_size": len(filtered),
            "output_files": {
                "json": str(json_path),
                "csv": str(csv_path) if filtered else None,
            },
        }
        lineage_path = out_dir / "lineage.json"
        with open(lineage_path, "w", encoding="utf-8") as f:
            json.dump(lineage, f, ensure_ascii=False, indent=2)

        result["output_dir"] = str(out_dir)
        return result
