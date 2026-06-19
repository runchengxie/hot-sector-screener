"""
hotspot_backtest.py — 热点追踪策略回测（v2）

方法论：
  每天从 ths_hot 提取热股的概念标签
  → 排名前N的概念作为"今日热点"
  → 买入这些概念对应的成分股（等权，用 kpl_concept_cons 映射）
  → 持有1天，次日再平衡
  → 对比沪深300（510300）

数据依赖：
  - ths_hot（576天，2024-01-02 ~ 2026-05-29）
  - kpl_concept_cons（394天，2024-10-14 ~ 2026-05-29）
  - daily A股日线（至2026-06-08）
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

DATA_ROOT = Path(os.environ.get("DATA_PLATFORM_ROOT", "/data/market-data-platform"))


# ── data helpers ──

def _resolve_data_dir(source: str) -> Path | None:
    base = DATA_ROOT / "assets" / "tushare" / "a_share" / source
    if not base.is_dir():
        return None
    subdirs = sorted(d for d in base.iterdir() if d.is_dir())
    if not subdirs:
        return None
    data_dir = subdirs[-1] / "data"
    return data_dir if data_dir.is_dir() else subdirs[-1]


def _load_partition(base_dir: Path, trade_date: str) -> pd.DataFrame:
    date_clean = trade_date.replace("-", "")
    part = base_dir / f"trade_date={date_clean}"
    if not part.is_dir():
        return pd.DataFrame()
    try:
        return pd.read_parquet(part)
    except Exception:
        return pd.DataFrame()


def load_hot_stocks(trade_date: str, top_n: int = 30) -> pd.DataFrame:
    d = _resolve_data_dir("ths_hot")
    if d is None:
        return pd.DataFrame()
    df = _load_partition(d, trade_date)
    if df.empty:
        return df
    df = df.sort_values("rank").head(top_n).copy()

    # Parse concept field — it's stored as a string representation of a list
    concepts = []
    for _, row in df.iterrows():
        raw = str(row.get("concept", ""))
        # Strip brackets, split by comma
        raw = raw.strip().strip("[]").strip('"').strip("'")
        parts = re.split(r'[",，]\s*', raw)
        parts = [p.strip().strip('"').strip("'") for p in parts if p.strip()]
        concepts.append([p for p in parts if p and p not in ("", "[", "]")])
    df["concept_list"] = concepts
    return df


def load_kpl_concept_cons(trade_date: str) -> pd.DataFrame:
    """Load kpl_concept_cons — has 394 days covering 2024-10-14 to 2026-05-29."""
    d = _resolve_data_dir("kpl_concept_cons")
    if d is None:
        return pd.DataFrame()
    return _load_partition(d, trade_date)


def load_daily_data(trade_date: str) -> pd.DataFrame:
    d = _resolve_data_dir("daily")
    if d is None:
        return pd.DataFrame()
    return _load_partition(d, trade_date)


# ── backtest core ──

def run_backtest(
    start_date: str = "2024-10-14",
    end_date: str = "2026-05-01",
    top_concepts: int = 3,
    stocks_per_concept: int = 10,
    top_n_hot_stocks: int = 30,
    initial_capital: float = 1_000_000,
    sample_every_n_days: int = 3,
) -> dict:
    # Build date list from ths_hot
    d = _resolve_data_dir("ths_hot")
    if d is None:
        return {"error": "ths_hot not found"}
    all_dates = sorted(
        e.name.split("=", 1)[1]
        for e in d.iterdir()
        if e.name.startswith("trade_date=")
    )
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")
    all_dates = [d_ for d_ in all_dates if s <= d_ <= e]
    sampled = all_dates[::sample_every_n_days]
    print(f"  Range: {start_date} ~ {end_date}")
    print(f"  Trading days: {len(all_dates)}, sampled: {len(sampled)}", flush=True)

    # Pre-check kpl data availability
    kpl_dir = _resolve_data_dir("kpl_concept_cons")
    kpl_dates = set()
    if kpl_dir:
        for entry in kpl_dir.iterdir():
            if entry.name.startswith("trade_date="):
                kpl_dates.add(entry.name.split("=", 1)[1])
    print(f"  kpl_concept_cons available days: {len(kpl_dates)}", flush=True)

    nav = initial_capital
    daily_returns = []
    concept_history = []
    trade_count = 0
    hit_count = 0

    for date_str in sampled:
        dt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

        # 1. Hot stocks → extract concepts
        hot = load_hot_stocks(dt, top_n=top_n_hot_stocks)
        if hot.empty:
            continue
        all_concepts = []
        for cl in hot["concept_list"]:
            all_concepts.extend(cl)
        concept_freq = pd.Series(all_concepts).value_counts()
        if concept_freq.empty:
            continue

        top_c_list = concept_freq.head(top_concepts).index.tolist()
        concept_history.append({
            "date": dt,
            "top_concepts": top_c_list,
            "freqs": [int(v) for v in concept_freq.head(top_concepts).values],
        })

        # 2. Map concepts to stocks via kpl
        kpl = load_kpl_concept_cons(dt)
        if kpl.empty:
            continue

        # Build a lookup: con_name → set of ts_code
        kpl_lookup: dict[str, set[str]] = {}
        for _, row in kpl.iterrows():
            con_name = str(row.get("con_name", "")).strip()
            code = str(row.get("ts_code", "")).strip()
            if con_name and code:
                kpl_lookup.setdefault(con_name, set()).add(code)

        # Match concepts
        candidate_codes: list[str] = []
        for concept in top_c_list:
            matched = False
            for kpl_name, codes in kpl_lookup.items():
                if concept.lower() in kpl_name.lower() or kpl_name.lower() in concept.lower():
                    candidate_codes.extend(list(codes)[:stocks_per_concept])
                    matched = True
            if not matched:
                # Try fuzzy match via kpl desc field
                desc_match = kpl[kpl["desc"].str.contains(re.escape(concept), case=False, na=False)]
                if not desc_match.empty:
                    candidate_codes.extend(desc_match["ts_code"].unique().tolist()[:stocks_per_concept])

        candidate_codes = list(dict.fromkeys(candidate_codes))
        if not candidate_codes:
            continue

        # 3. Entry: next trading day open
        next_idx = all_dates.index(date_str) + 1
        if next_idx >= len(all_dates):
            continue
        entry_date = all_dates[next_idx]
        entry_dt = f"{entry_date[:4]}-{entry_date[4:6]}-{entry_date[6:]}"
        entry_df = load_daily_data(entry_dt)
        if entry_df.empty:
            continue

        entry_prices = entry_df[entry_df["ts_code"].isin(candidate_codes)]
        if entry_prices.empty:
            continue
        entry_series = entry_prices.set_index("ts_code")["open"].astype(float)
        if entry_series.empty:
            continue

        # 4. Exit: next-next day
        exit_idx = next_idx + 1
        if exit_idx >= len(all_dates):
            continue
        exit_date = all_dates[exit_idx]
        exit_dt = f"{exit_date[:4]}-{exit_date[4:6]}-{exit_date[6:]}"
        exit_df = load_daily_data(exit_dt)
        if exit_df.empty:
            continue

        exit_series = exit_df[exit_df["ts_code"].isin(entry_series.index)].set_index("ts_code")["open"].astype(float)
        common = entry_series.index.intersection(exit_series.index)
        if len(common) < 3:
            continue  # too few stocks survived

        ret = (exit_series[common] / entry_series[common] - 1).mean()
        daily_returns.append(float(ret))
        trade_count += 1
        if ret > 0:
            hit_count += 1
        nav *= (1 + ret)

        if trade_count % 20 == 0:
            print(f"    ... {trade_count} trades, current NAV={nav:.0f}", flush=True)

    # ── compute metrics ──
    ret_arr = np.array(daily_returns)
    n = len(ret_arr)
    total_ret = float(np.prod(1 + ret_arr) - 1) if n > 0 else 0
    ann_ret = float((1 + total_ret) ** (252 / n) - 1) if n > 0 else 0
    vol = float(np.std(ret_arr) * np.sqrt(252)) if n > 1 else 0
    sharpe = float(ann_ret / vol) if vol > 0 else 0
    hit_rate = float(hit_count / n) if n > 0 else 0
    max_dd = float(_max_drawdown(ret_arr))

    # CSI 300 benchmark: buy & hold 510300
    bm_ret = _benchmark_return(sampled, all_dates)
    if bm_ret is not None:
        excess = total_ret - bm_ret["total_return"]
    else:
        excess = None

    return {
        "period": f"{start_date} to {end_date}",
        "config": {
            "trading_days": n,
            "top_concepts": top_concepts,
            "stocks_per_concept": stocks_per_concept,
            "top_n_hot_stocks": top_n_hot_stocks,
            "sample_every_n_days": sample_every_n_days,
        },
        "strategy": {
            "total_return_pct": round(total_ret * 100, 2),
            "annual_return_pct": round(ann_ret * 100, 2),
            "annual_vol_pct": round(vol * 100, 2),
            "sharpe": round(sharpe, 3),
            "hit_rate_pct": round(hit_rate * 100, 1),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "trade_count": n,
            "final_nav": round(nav, 0),
        },
        "benchmark": {
            "label": "沪深300 ETF (510300) 等权买入持有",
            "total_return_pct": round(bm_ret["total_return"] * 100, 2) if bm_ret else None,
            "annual_return_pct": round(bm_ret["annual_return"] * 100, 2) if bm_ret else None,
        },
        "excess_return_pct": round(excess * 100, 2) if excess is not None else None,
        "concept_samples": concept_history[:10],
    }


def _max_drawdown(returns: np.ndarray) -> float:
    if len(returns) == 0:
        return 0.0
    cum = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    return float(np.min(dd))


def _benchmark_return(sampled_dates: list[str], all_dates: list[str]) -> dict | None:
    """Simple CSI 300 (510300.SH) proxy: use average of a_share daily returns as market proxy."""
    entry_returns = []
    for i, d in enumerate(sampled_dates):
        idx = all_dates.index(d) + 1
        if idx >= len(all_dates):
            continue
        entry_d = all_dates[idx]
        entry_dt = f"{entry_d[:4]}-{entry_d[4:6]}-{entry_d[6:]}"
        df = load_daily_data(entry_dt)
        if df.empty or "pct_chg" not in df.columns:
            continue
        chg = df["pct_chg"].astype(float).dropna()
        if len(chg) > 100:
            entry_returns.append(chg.mean() / 100)  # pct_chg is in percent

    if not entry_returns:
        return None
    arr = np.array(entry_returns)
    total = float(np.prod(1 + arr) - 1)
    ann = float((1 + total) ** (252 / len(arr)) - 1) if len(arr) > 1 else 0
    return {"total_return": total, "annual_return": ann, "days": len(arr)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-10-14")
    parser.add_argument("--end", default="2026-05-01")
    parser.add_argument("--top-concepts", type=int, default=3)
    parser.add_argument("--stocks-per-concept", type=int, default=10)
    parser.add_argument("--sample", type=int, default=3)
    args = parser.parse_args()

    result = run_backtest(
        start_date=args.start,
        end_date=args.end,
        top_concepts=args.top_concepts,
        stocks_per_concept=args.stocks_per_concept,
        sample_every_n_days=args.sample,
    )
    print("\n" + "=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))
