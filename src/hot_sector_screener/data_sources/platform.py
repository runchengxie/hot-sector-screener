from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

_A_SHARE_SOURCE_DIRS = {
    "ths_hot": ("assets", "tushare", "a_share", "ths_hot"),
    "dc_concept": ("assets", "tushare", "a_share", "dc_concept"),
    "dc_concept_cons": ("assets", "tushare", "a_share", "dc_concept_cons"),
    "kpl_concept_cons": ("assets", "tushare", "a_share", "kpl_concept_cons"),
    "kpl_list": ("assets", "tushare", "a_share", "kpl_list"),
    "limit_step": ("assets", "tushare", "a_share", "limit_step"),
    "limit_cpt_list": ("assets", "tushare", "a_share", "limit_cpt_list"),
    "limit_list_ths": ("assets", "tushare", "a_share", "limit_list_ths"),
    "hotspot_features": ("assets", "tushare", "a_share", "hotspot_features"),
    "daily": ("assets", "tushare", "a_share", "daily"),
}


def _resolve_platform_root() -> Path:
    root = os.environ.get("DATA_PLATFORM_ROOT")
    if not root:
        raise RuntimeError(
            "DATA_PLATFORM_ROOT 未设置。请指定数据湖路径：\n"
            "  export DATA_PLATFORM_ROOT=/home/richard/data/market-data-platform"
        )
    return Path(root).expanduser().resolve()


def _source_dir(source: str) -> Path:
    root = _resolve_platform_root()
    parts = _A_SHARE_SOURCE_DIRS[source]
    return root.joinpath(*parts)


def _resolve_latest_data_dir(base_dir: Path) -> Path | None:
    """Resolve the actual data directory containing Hive partitions.

    Handles the market-data-platform convention:
      <source>/a_share_all_<source>_latest/data/
    """
    if not base_dir.is_dir():
        return None

    candidates: list[Path] = []
    direct_data = base_dir / "data"
    if direct_data.is_dir():
        candidates.append(direct_data)
    for subdir in sorted(d for d in base_dir.iterdir() if d.is_dir()):
        data_dir = subdir / "data"
        candidates.append(data_dir if data_dir.is_dir() else subdir)

    partitioned = [path for path in candidates if any(path.glob("trade_date=*"))]
    if not partitioned:
        return None

    def latest_partition(path: Path) -> str:
        dates = sorted(
            entry.name.split("=", 1)[1]
            for entry in path.iterdir()
            if entry.is_dir() and entry.name.startswith("trade_date=")
        )
        return dates[-1] if dates else ""

    return max(partitioned, key=latest_partition)


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


def load_kpl_list(trade_date: str, limit: int = 300) -> pd.DataFrame:
    """Load 开盘啦涨停/炸板榜单 for a given trade date."""
    kpl_dir = _source_dir("kpl_list")
    if not kpl_dir.is_dir():
        return pd.DataFrame()
    return _load_hive_partitioned(
        kpl_dir,
        trade_date,
        columns=[
            "ts_code",
            "name",
            "trade_date",
            "lu_desc",
            "tag",
            "theme",
            "status",
            "pct_chg",
            "bid_amount",
            "amount",
            "turnover_rate",
        ],
    ).head(limit)


def load_limit_step(trade_date: str) -> pd.DataFrame:
    """Load A 股连板天梯 for a given trade date."""
    step_dir = _source_dir("limit_step")
    if not step_dir.is_dir():
        return pd.DataFrame()
    return _load_hive_partitioned(
        step_dir,
        trade_date,
        columns=["ts_code", "name", "trade_date", "nums"],
    )


def load_limit_cpt_list(trade_date: str) -> pd.DataFrame:
    """Load 涨停最强板块统计 for a given trade date."""
    cpt_dir = _source_dir("limit_cpt_list")
    if not cpt_dir.is_dir():
        return pd.DataFrame()
    return _load_hive_partitioned(
        cpt_dir,
        trade_date,
        columns=[
            "ts_code",
            "name",
            "trade_date",
            "days",
            "up_stat",
            "cons_nums",
            "up_nums",
            "pct_chg",
            "rank",
        ],
    )


def load_limit_list_ths(trade_date: str, limit: int = 300) -> pd.DataFrame:
    """Load 同花顺涨跌停明细 for a given trade date."""
    limit_dir = _source_dir("limit_list_ths")
    if not limit_dir.is_dir():
        return pd.DataFrame()
    return _load_hive_partitioned(
        limit_dir,
        trade_date,
        columns=[
            "ts_code",
            "name",
            "trade_date",
            "limit_type",
            "pct_chg",
            "turnover_rate",
            "free_float",
            "lu_desc",
            "tag",
            "status",
        ],
    ).head(limit)


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
    if source not in _A_SHARE_SOURCE_DIRS:
        return []
    source_dir = _source_dir(source)
    if not source_dir.is_dir():
        return []
    data_dir = _resolve_latest_data_dir(source_dir)
    if data_dir is None or not data_dir.is_dir():
        return []
    dates: list[str] = []
    for entry in data_dir.iterdir():
        if entry.name.startswith("trade_date="):
            dates.append(entry.name.split("=", 1)[1])
    return sorted(dates)


def latest_common_date(
    sources: list[str] | tuple[str, ...],
    *,
    not_after: str | None = None,
) -> str | None:
    """Return the latest trade date available across all requested sources."""
    common_dates: set[str] | None = None
    for source in sources:
        dates = set(list_available_dates(source))
        if not dates:
            return None
        common_dates = dates if common_dates is None else common_dates & dates
        if not common_dates:
            return None
    if not common_dates:
        return None
    if not_after is not None:
        common_dates = {trade_date for trade_date in common_dates if trade_date <= not_after}
    if not common_dates:
        return None
    return sorted(common_dates)[-1]


def summarize_data_coverage() -> dict[str, Any]:
    """Summarise what hotspot data is available in the data lake."""
    sources = [
        "ths_hot",
        "dc_concept",
        "dc_concept_cons",
        "kpl_concept_cons",
        "kpl_list",
        "limit_step",
        "limit_cpt_list",
        "limit_list_ths",
        "hotspot_features",
        "daily",
    ]
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
