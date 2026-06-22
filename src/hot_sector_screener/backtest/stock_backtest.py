"""
stock_backtest.py — 热点追踪概念→个股回测

方法论：
  每天从 ths_hot 提取热股的概念标签
  → 排名前N的概念作为"今日热点"
  → 买入这些概念对应的成分股（等权，用 kpl_concept_cons 映射）
  → 持有1天，次日再平衡
  → 对比沪深300（510300 代理）

数据依赖：
  - ths_hot（576天，2024-01-02 ~ 2026-05-29）
  - kpl_concept_cons（394天，2024-10-14 ~ 2026-05-29）
  - daily A股日线（至2026-06-08）
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from ..data_sources.platform import (
    list_available_dates,
    load_daily_data,
    load_kpl_concept_cons,
    load_ths_hot,
)
from .metrics import (  # noqa: F401 — max_drawdown used by compute_metrics
    compute_metrics,
    max_drawdown,
)

# ── private helpers ──


def _fmt_date(date_str: str) -> str:
    """Convert YYYYMMDD → YYYY-MM-DD."""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


def _build_date_list(
    start_date: str, end_date: str, sample_every_n_days: int
) -> tuple[list[str], list[str]]:
    """Build full and sampled date lists from ths_hot data-lake partitions.

    Returns:
        (all_dates, sampled_dates) — both lists of YYYYMMDD strings.
    """
    all_dates = list_available_dates("ths_hot")
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")
    all_dates = [d for d in all_dates if s <= d <= e]
    sampled = all_dates[::sample_every_n_days]

    print(f"  Range: {start_date} ~ {end_date}")
    print(f"  Trading days: {len(all_dates)}, sampled: {len(sampled)}", flush=True)
    return all_dates, sampled


def _extract_top_concepts(
    dt: str, top_n_hot_stocks: int, top_concepts: int
) -> tuple[list[str] | None, dict[str, Any] | None]:
    """Load ths_hot, parse concept strings, and return the top-N most frequent concepts.

    Returns:
        (top_c_list, concept_history_entry) or (None, None) if no concepts found.
    """
    hot = load_ths_hot(dt, limit=200)
    if hot.empty:
        return None, None

    # Sort by rank (matching original behaviour) and keep top-N
    hot = hot.sort_values("rank").head(top_n_hot_stocks)

    # Parse concept field — stored as a string representation of a list
    concepts_list: list[list[str]] = []
    for _, row in hot.iterrows():
        raw = str(row.get("concept", ""))
        raw = raw.strip().strip("[]").strip('"').strip("'")
        parts = re.split(r'[",，]\s*', raw)
        parts = [p.strip().strip('"').strip("'") for p in parts if p.strip()]
        concepts_list.append([p for p in parts if p and p not in ("", "[", "]")])

    # Flatten and count frequencies
    all_concepts: list[str] = []
    for cl in concepts_list:
        all_concepts.extend(cl)
    concept_freq = pd.Series(all_concepts).value_counts()
    if concept_freq.empty:
        return None, None

    top_c_list = concept_freq.head(top_concepts).index.tolist()
    ch_entry = {
        "date": dt,
        "top_concepts": top_c_list,
        "freqs": [int(v) for v in concept_freq.head(top_concepts).values],
    }
    return top_c_list, ch_entry


def _map_concepts_to_candidates(
    top_c_list: list[str], dt: str, stocks_per_concept: int
) -> list[str]:
    """Map concept names to candidate stock codes via kpl_concept_cons.

    kpl schema: ts_code=concept_code (e.g. '000025.KP'),
                con_code=stock_code (e.g. '000977.SZ'),
                name=concept_name (e.g. 'AI算力概念'),
                con_name=stock_name (e.g. '浪潮信息')

    Strategy: match concept name against kpl 'name' column (concept names),
    then return all stock codes (con_code) in matched concepts.
    Falls back to desc-field fuzzy match.
    """
    kpl = load_kpl_concept_cons(dt)
    if kpl.empty:
        return []

    # Build lookup: concept_code (ts_code) → set of stock_codes (con_code)
    concept_to_stocks: dict[str, set[str]] = {}
    concept_names: dict[str, str] = {}  # concept_code → concept_name
    for _, row in kpl.iterrows():
        cc = str(row.get("ts_code", "")).strip()  # concept code
        sc = str(row.get("con_code", "")).strip()  # stock code
        cn = str(row.get("name", "")).strip()  # concept name
        if cc and sc:
            concept_to_stocks.setdefault(cc, set()).add(sc)
        if cc and cn:
            concept_names[cc] = cn

    candidate_codes: list[str] = []
    for concept in top_c_list:
        matched = False
        # 1. Match concept name against kpl 'name' (concept names)
        for cc, cn in concept_names.items():
            if concept.lower() in cn.lower() or cn.lower() in concept.lower():
                codes = concept_to_stocks.get(cc, set())
                candidate_codes.extend(list(codes)[:stocks_per_concept])
                matched = True
        if not matched:
            # 2. Try fuzzy match via kpl desc field (stock descriptions)
            desc_match = kpl[kpl["desc"].str.contains(re.escape(concept), case=False, na=False)]
            if not desc_match.empty:
                codes_from_desc = list(
                    dict.fromkeys(
                        desc_match["con_code"].astype(str).tolist()  # stock codes
                    )
                )[:stocks_per_concept]
                candidate_codes.extend(codes_from_desc)

    # Deduplicate while preserving order
    return list(dict.fromkeys(candidate_codes))


def _compute_entry_exit(
    candidate_codes: list[str], date_str: str, all_dates: list[str]
) -> float | None:
    """Compute equal-weight return: next-day open entry → day-after open exit.

    Returns:
        Mean return for surviving stocks, or None if the trade cannot be executed
        (no price data, missing dates, fewer than 3 matched stocks).
    """
    try:
        date_idx = all_dates.index(date_str)
    except ValueError:
        return None

    # Entry: next trading day
    next_idx = date_idx + 1
    if next_idx >= len(all_dates):
        return None

    entry_date = all_dates[next_idx]
    entry_dt = _fmt_date(entry_date)
    entry_df = load_daily_data(entry_dt)
    if entry_df.empty:
        return None

    entry_prices = entry_df[entry_df["ts_code"].isin(candidate_codes)]
    if entry_prices.empty:
        return None
    entry_series = entry_prices.set_index("ts_code")["open"].astype(float)
    if entry_series.empty:
        return None

    # Exit: day after entry
    exit_idx = next_idx + 1
    if exit_idx >= len(all_dates):
        return None

    exit_date = all_dates[exit_idx]
    exit_dt = _fmt_date(exit_date)
    exit_df = load_daily_data(exit_dt)
    if exit_df.empty:
        return None

    exit_series = (
        exit_df[exit_df["ts_code"].isin(entry_series.index)]
        .set_index("ts_code")["open"]
        .astype(float)
    )
    common = entry_series.index.intersection(exit_series.index)
    if len(common) < 3:
        return None  # too few stocks survived

    return float((exit_series[common] / entry_series[common] - 1).mean())


def _benchmark_market_return(
    sampled_dates: list[str], all_dates: list[str]
) -> dict[str, Any] | None:
    """Market-average daily pct_chg proxy (no real CSI 300 index in data lake).

    For each sampled date, takes the next trading day's market-wide average
    pct_chg across all A-shares as a broad market proxy.
    """
    entry_returns: list[float] = []
    for d in sampled_dates:
        try:
            idx = all_dates.index(d)
        except ValueError:
            continue
        if idx + 1 >= len(all_dates):
            continue

        entry_d = all_dates[idx + 1]
        entry_dt = _fmt_date(entry_d)
        df = load_daily_data(entry_dt)
        if df.empty or "pct_chg" not in df.columns:
            continue

        chg = df["pct_chg"].astype(float).dropna()
        if len(chg) > 100:
            entry_returns.append(float(chg.mean()) / 100)  # pct_chg is in percent

    if not entry_returns:
        return None

    arr = np.array(entry_returns)
    total = float(np.prod(1 + arr) - 1)
    ann = float((1 + total) ** (252 / len(arr)) - 1) if len(arr) > 1 else 0.0
    return {"total_return": total, "annual_return": ann, "days": len(arr)}


def _build_result(
    daily_returns: list[float],
    concept_history: list[dict[str, Any]],
    nav: float,
    start_date: str,
    end_date: str,
    top_concepts: int,
    stocks_per_concept: int,
    top_n_hot_stocks: int,
    sample_every_n_days: int,
    initial_capital: float,
    sampled: list[str],
    all_dates: list[str],
) -> dict[str, Any]:
    """Assemble the final result dict from backtest outputs.

    Uses shared `compute_metrics` from the backtest package; renames the
    `trade_days` key to `trade_count` to match the original return format.
    """
    # Strategy metrics via shared compute_metrics
    strategy_metrics = compute_metrics(daily_returns, initial_capital)
    strategy_metrics["trade_count"] = strategy_metrics.pop("trade_days")

    # CSI 300 proxy: market-average daily pct_chg (no real CSI 300 data in lake)
    bm_ret = _benchmark_market_return(sampled, all_dates)
    total_ret = float(np.prod(1 + np.array(daily_returns)) - 1) if daily_returns else 0.0
    excess = total_ret - bm_ret["total_return"] if bm_ret is not None and daily_returns else None

    # Compute beta / alpha vs benchmark proxy
    beta = None
    alpha = None
    if bm_ret is not None and daily_returns and bm_ret.get("days", 0) >= 10:
        bm_sample = _benchmark_market_return(sampled, all_dates)
        if bm_sample:
            # Reconstruct benchmark return series for beta calc
            # Use market-average returns aligned with strategy trades
            bm_series: list[float] = []
            for d in sampled:
                try:
                    idx = all_dates.index(d)
                except ValueError:
                    continue
                if idx + 1 >= len(all_dates):
                    continue
                df = load_daily_data(_fmt_date(all_dates[idx + 1]))
                if df.empty or "pct_chg" not in df.columns:
                    continue
                chg = df["pct_chg"].astype(float).dropna()
                if len(chg) > 100:
                    bm_series.append(float(chg.mean()) / 100)
            if len(bm_series) >= 10:
                min_len = min(len(daily_returns), len(bm_series))
                s = np.array(daily_returns[:min_len])
                b = np.array(bm_series[:min_len])
                cov = float(np.cov(s, b)[0, 1])
                var = float(np.var(b))
                if var > 0:
                    beta = round(cov / var, 3)
                    bm_total = float(np.prod(1 + b) - 1)
                    bm_ann = float((1 + bm_total) ** (252 / min_len) - 1) if min_len > 0 else 0.0
                    strat_ann = (
                        float((1 + total_ret) ** (252 / len(daily_returns)) - 1)
                        if daily_returns
                        else 0.0
                    )
                    alpha = round((strat_ann - beta * bm_ann) * 100, 2)

    return {
        "period": f"{start_date} to {end_date}",
        "config": {
            "trading_days": len(daily_returns),
            "top_concepts": top_concepts,
            "stocks_per_concept": stocks_per_concept,
            "top_n_hot_stocks": top_n_hot_stocks,
            "sample_every_n_days": sample_every_n_days,
        },
        "strategy": strategy_metrics,
        "benchmark": {
            "label": "全A等权 (daily pct_chg均值, CSI300代理)",
            "total_return_pct": round(bm_ret["total_return"] * 100, 2) if bm_ret else None,
            "annual_return_pct": round(bm_ret["annual_return"] * 100, 2) if bm_ret else None,
        },
        "excess_return_pct": round(excess * 100, 2) if excess is not None else None,
        "beta": beta,
        "alpha_pct": alpha,
        "concept_samples": concept_history[:10],
    }


# ── public API ──


def run_stock_backtest(
    start_date: str = "2024-10-14",
    end_date: str = "2026-05-01",
    top_concepts: int = 3,
    stocks_per_concept: int = 10,
    top_n_hot_stocks: int = 30,
    initial_capital: float = 1_000_000,
    sample_every_n_days: int = 3,
) -> dict[str, Any]:
    """Run a hotspot-concept-to-stocks backtest.

    Methodology:
      1. Build a trading-date list from the ths_hot data lake.
      2. Each sampled day, extract the top-N most frequent concepts from the
         ranked hot stocks.
      3. Map each concept to constituent stocks via kpl_concept_cons
         (substring matching on concept name, with desc-field fallback).
      4. Buy equal-weight at next-day open, sell at day-after open.
      5. Compare against market-average proxy (no real CSI 300 in data lake).

    Returns:
        dict with keys: period, config, strategy, benchmark,
        excess_return_pct, beta, alpha_pct, concept_samples.
    """
    # 1. Build date list
    all_dates, sampled = _build_date_list(start_date, end_date, sample_every_n_days)
    if not sampled:
        return {"error": "No trading dates in range"}

    # Pre-check kpl data availability
    kpl_dates = set(list_available_dates("kpl_concept_cons"))
    print(f"  kpl_concept_cons available days: {len(kpl_dates)}", flush=True)

    # 2. Run simulation
    daily_returns: list[float] = []
    concept_history: list[dict[str, Any]] = []
    nav = initial_capital
    trade_count = 0

    for date_str in sampled:
        dt = _fmt_date(date_str)

        # 2a. Extract top concepts
        top_c_list, ch_entry = _extract_top_concepts(dt, top_n_hot_stocks, top_concepts)
        if top_c_list is None or ch_entry is None:
            continue
        concept_history.append(ch_entry)

        # 2b. Map concepts to candidate stocks
        candidate_codes = _map_concepts_to_candidates(top_c_list, dt, stocks_per_concept)
        if not candidate_codes:
            continue

        # 2c. Compute entry→exit return
        ret = _compute_entry_exit(candidate_codes, date_str, all_dates)
        if ret is None:
            continue

        daily_returns.append(ret)
        trade_count += 1
        nav *= 1 + ret

        if trade_count % 20 == 0:
            print(f"    ... {trade_count} trades, current NAV={nav:.0f}", flush=True)

    # 3. Build and return result
    return _build_result(
        daily_returns=daily_returns,
        concept_history=concept_history,
        nav=nav,
        start_date=start_date,
        end_date=end_date,
        top_concepts=top_concepts,
        stocks_per_concept=stocks_per_concept,
        top_n_hot_stocks=top_n_hot_stocks,
        sample_every_n_days=sample_every_n_days,
        initial_capital=initial_capital,
        sampled=sampled,
        all_dates=all_dates,
    )
