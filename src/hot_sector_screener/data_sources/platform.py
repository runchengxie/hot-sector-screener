from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd


def _resolve_platform_root() -> Path:
    root = os.environ.get("DATA_PLATFORM_ROOT")
    if not root:
        raise RuntimeError(
            "DATA_PLATFORM_ROOT 未设置。请指定数据湖路径：\n"
            "  export DATA_PLATFORM_ROOT=/home/richard/data/market-data-platform"
        )
    return Path(root).expanduser().resolve()


def _resolve_latest_data_dir(base_dir: Path) -> Path | None:
    """Resolve the actual data directory containing Hive partitions.

    Handles the market-data-platform convention:
      <source>/a_share_all_<source>_latest/data/
    """
    if not base_dir.is_dir():
        return None
    # Find the latest subdirectory (there should be only one: a_share_all_*_latest)
    subdirs = [d for d in base_dir.iterdir() if d.is_dir()]
    if not subdirs:
        return None
    # Pick the first (should be the latest)
    latest = sorted(subdirs)[-1]
    data_dir = latest / "data"
    return data_dir if data_dir.is_dir() else latest


def _load_hive_partitioned(
    base_dir: Path, trade_date: str, columns: list[str] | None = None
) -> pd.DataFrame:
    """Load one Hive partition from a market-data-platform source directory.

    Handles the convention:
      <base_dir>/a_share_all_<name>_latest/data/trade_date=YYYYMMDD/*.parquet
    """
    data_dir = _resolve_latest_data_dir(base_dir)
    if data_dir is None:
        return pd.DataFrame()

    date_clean = trade_date.replace("-", "")
    partition_dir = data_dir / f"trade_date={date_clean}"
    if not partition_dir.is_dir():
        return pd.DataFrame()
    try:
        return pd.read_parquet(partition_dir, columns=columns)
    except Exception:
        return pd.DataFrame()


def load_ths_hot(trade_date: str, limit: int = 100) -> pd.DataFrame:
    """Load 同花顺热榜 for a given trade date.

    Returns columns: ts_code, ts_name, rank, hot, concept, pct_change, rank_reason
    """
    root = _resolve_platform_root()
    ths_dir = root / "assets" / "tushare" / "a_share" / "ths_hot"
    if not ths_dir.is_dir():
        return pd.DataFrame()
    return _load_hive_partitioned(
        ths_dir,
        trade_date,
        columns=[
            "trade_date",
            "ts_code",
            "ts_name",
            "rank",
            "hot",
            "concept",
            "pct_change",
            "rank_reason",
        ],
    ).head(limit)


def load_dc_concept(trade_date: str) -> pd.DataFrame:
    """Load 东方财富概念板块 for a given trade date.

    Returns columns: theme_code, name, strength, hot, lead_stock, lead_stock_code
    """
    root = _resolve_platform_root()
    dc_dir = root / "assets" / "tushare" / "a_share" / "dc_concept"
    if not dc_dir.is_dir():
        return pd.DataFrame()
    return _load_hive_partitioned(
        dc_dir,
        trade_date,
        columns=[
            "theme_code",
            "trade_date",
            "name",
            "pct_change",
            "hot",
            "sort",
            "strength",
            "z_t_num",
            "main_change",
            "lead_stock",
            "lead_stock_code",
            "lead_stock_pct_change",
        ],
    )


def load_dc_concept_cons(trade_date: str) -> pd.DataFrame:
    """Load 东财概念成分股 for a given trade date.

    Returns columns: ts_code, name, theme_code, industry, hot_num
    """
    root = _resolve_platform_root()
    cons_dir = root / "assets" / "tushare" / "a_share" / "dc_concept_cons"
    if not cons_dir.is_dir():
        return pd.DataFrame()
    return _load_hive_partitioned(
        cons_dir,
        trade_date,
        columns=["ts_code", "name", "theme_code", "trade_date", "industry", "hot_num"],
    )


def load_kpl_concept_cons(trade_date: str) -> pd.DataFrame:
    """Load 开盘啦概念成分 for a given trade date."""
    root = _resolve_platform_root()
    kpl_dir = root / "assets" / "tushare" / "a_share" / "kpl_concept_cons"
    if not kpl_dir.is_dir():
        return pd.DataFrame()
    return _load_hive_partitioned(
        kpl_dir,
        trade_date,
        columns=["ts_code", "name", "con_name", "con_code", "trade_date", "desc", "hot_num"],
    )


def load_daily_data(trade_date: str) -> pd.DataFrame:
    """Load daily A-share stock price data for a given trade date.

    Returns columns: ts_code, trade_date, open, high, low, close, pct_chg, ...
    """
    root = _resolve_platform_root()
    daily_dir = root / "assets" / "tushare" / "a_share" / "daily"
    if not daily_dir.is_dir():
        return pd.DataFrame()
    return _load_hive_partitioned(daily_dir, trade_date)


def load_hotspot_features(trade_date: str) -> pd.DataFrame:
    """Load derived hotspot features for a given trade date.

    Returns 19 feature columns for hotspot-aware ranking.
    """
    root = _resolve_platform_root()
    hf_dir = root / "assets" / "tushare" / "a_share" / "hotspot_features"
    if not hf_dir.is_dir():
        return pd.DataFrame()
    return _load_hive_partitioned(hf_dir, trade_date)


def list_available_dates(source: str = "ths_hot") -> list[str]:
    """List available trade dates for a given hotspot data source."""
    root = _resolve_platform_root()
    source_map = {
        "ths_hot": root / "assets" / "tushare" / "a_share" / "ths_hot",
        "dc_concept": root / "assets" / "tushare" / "a_share" / "dc_concept",
        "dc_concept_cons": root / "assets" / "tushare" / "a_share" / "dc_concept_cons",
        "kpl_concept_cons": root / "assets" / "tushare" / "a_share" / "kpl_concept_cons",
        "hotspot_features": root / "assets" / "tushare" / "a_share" / "hotspot_features",
    }
    source_dir = source_map.get(source)
    if source_dir is None or not source_dir.is_dir():
        return []
    data_dir = _resolve_latest_data_dir(source_dir)
    if data_dir is None or not data_dir.is_dir():
        return []
    dates: list[str] = []
    for entry in data_dir.iterdir():
        if entry.name.startswith("trade_date="):
            dates.append(entry.name.split("=", 1)[1])
    return sorted(dates)


def summarize_data_coverage() -> dict[str, Any]:
    """Summarise what hotspot data is available in the data lake."""
    sources = ["ths_hot", "dc_concept", "dc_concept_cons", "kpl_concept_cons", "hotspot_features"]
    result: dict[str, Any] = {}
    for source in sources:
        dates = list_available_dates(source)
        result[source] = {
            "available_dates": len(dates),
            "earliest": dates[0] if dates else None,
            "latest": dates[-1] if dates else None,
            "sample_dates": dates[-5:] if len(dates) >= 5 else dates,
        }
    return result
