"""Data sources — data-lake access layer for hot-sector-screener."""

from .platform import (
    list_available_dates,
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
    summarize_data_coverage,
)
from .rotation_signal import load_industry_signal

__all__ = [
    "list_available_dates",
    "load_daily_data",
    "load_dc_concept",
    "load_dc_concept_cons",
    "load_hotspot_features",
    "load_industry_signal",
    "load_kpl_concept_cons",
    "load_kpl_list",
    "load_limit_cpt_list",
    "load_limit_list_ths",
    "load_limit_step",
    "load_ths_hot",
    "summarize_data_coverage",
]
