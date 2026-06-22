"""
etf_backtest.py — 热点概念→ETF 轮动回测

方法论：
  每天从 ths_hot 提取排名前N的概念
  → 概念→曝光维度映射（半导体→semiconductor, AI→tech 等）
  → 匹配 ETF metadata exposures → ETF 打分
  → 买入 top K ETF（等权）
  → 持有至下一交易日
  → 对比沪深300 (510300)

数据：
  - ths_hot（数据湖，576天）
  - ETF 日线（rotation-v3 下载，18只，378天）
  - ETF metadata（exposures 映射）
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from ..data_sources.platform import load_ths_hot
from .metrics import compute_metrics, yearly_breakdown

# ── Paths ──

ROTATION_ROOT = Path(os.environ.get("ETF_ROTATION_ROOT", "/home/richard/code/guan-etf-rotation-v3"))

# ── ETF metadata (hardcoded from etf_metadata.yml) ──

ETF_METADATA: dict[str, dict[str, Any]] = {
    "162719": {
        "name": "广发道琼斯美国石油开发",
        "exposures": {"overseas_equity": 1.0, "us_equity": 1.0, "commodity_oil": 0.9},
    },
    "159915": {
        "name": "创业板ETF",
        "exposures": {"china_equity": 1.0, "growth": 1.0, "tech": 0.45, "broad_market": 0.4},
    },
    "159985": {
        "name": "豆粕ETF",
        "exposures": {"commodity_agriculture": 1.0},
    },
    "515030": {
        "name": "新能源车ETF",
        "exposures": {"china_equity": 1.0, "new_energy": 1.0, "growth": 0.8, "tech": 0.35},
    },
    "513060": {
        "name": "恒生医疗ETF",
        "exposures": {"hongkong_equity": 1.0, "healthcare": 1.0, "growth": 0.5},
    },
    "512560": {
        "name": "中证军工ETF",
        "exposures": {"china_equity": 1.0, "defense": 1.0, "growth": 0.4},
    },
    "516510": {
        "name": "云计算ETF",
        "exposures": {"china_equity": 1.0, "tech": 1.0, "growth": 0.8},
    },
    "515880": {
        "name": "通信ETF",
        "exposures": {"china_equity": 1.0, "tech": 0.85, "growth": 0.5, "telecom": 0.7},
    },
    "515790": {
        "name": "光伏ETF",
        "exposures": {"china_equity": 1.0, "new_energy": 1.0, "growth": 0.8},
    },
    "513100": {
        "name": "纳指ETF",
        "exposures": {"us_equity": 1.0, "tech": 0.75, "growth": 0.75},
    },
    "512100": {
        "name": "中证1000ETF",
        "exposures": {"china_equity": 1.0, "broad_market": 0.7, "growth": 0.35},
    },
    "512690": {
        "name": "酒ETF",
        "exposures": {"china_equity": 1.0, "consumer": 1.0, "value": 0.35, "liquor": 0.9},
    },
    "513330": {
        "name": "恒生互联网ETF",
        "exposures": {"hongkong_equity": 1.0, "tech": 0.85, "growth": 0.8, "internet": 0.9},
    },
    "518880": {
        "name": "黄金ETF",
        "exposures": {"commodity_gold": 1.0},
    },
    "161128": {
        "name": "标普科技LOF",
        "exposures": {"us_equity": 1.0, "tech": 1.0, "growth": 0.85},
    },
    "513300": {
        "name": "纳斯达克100ETF",
        "exposures": {"us_equity": 1.0, "tech": 0.75, "growth": 0.75},
    },
    "513310": {
        "name": "中韩半导体ETF",
        "exposures": {"semiconductor": 1.0, "tech": 1.0, "growth": 0.8},
    },
    "513880": {
        "name": "日经225ETF",
        "exposures": {"japan_equity": 1.0, "broad_market": 0.6},
    },
    # Additional ETFs from rotation-v3 full pool
    "515100": {
        "name": "红利100ETF",
        "exposures": {"dividend_value": 1.0, "value": 0.8, "china_equity": 0.8},
    },
    "515980": {
        "name": "人工智能ETF",
        "exposures": {"tech": 1.0, "growth": 0.8, "china_equity": 0.9},
    },
    "512480": {
        "name": "半导体ETF",
        "exposures": {"semiconductor": 1.0, "tech": 1.0, "growth": 0.7, "china_equity": 0.9},
    },
    "512980": {
        "name": "传媒ETF",
        "exposures": {"internet": 0.8, "consumer": 0.6, "tech": 0.6, "china_equity": 0.9},
    },
    "513690": {
        "name": "恒生高股息ETF",
        "exposures": {"hongkong_equity": 1.0, "dividend_value": 0.9, "value": 0.7},
    },
    "516640": {
        "name": "芯片ETF",
        "exposures": {"semiconductor": 1.0, "tech": 1.0, "growth": 0.7},
    },
    "561910": {
        "name": "电池ETF",
        "exposures": {"new_energy": 0.9, "growth": 0.7, "china_equity": 1.0},
    },
    "588000": {
        "name": "科创50ETF",
        "exposures": {"tech": 0.9, "growth": 0.9, "semiconductor": 0.5, "china_equity": 0.9},
    },
    "159329": {
        "name": "纳斯达克ETF",
        "exposures": {"us_equity": 1.0, "tech": 0.8, "growth": 0.7},
    },
    "159919": {
        "name": "沪深300ETF",
        "exposures": {"china_equity": 1.0, "broad_market": 1.0, "value": 0.5},
    },
}

# Concept keyword → exposure dimension mapping
# Each concept is mapped to one or more exposure dimensions with weights
CONCEPT_EXPOSURE_MAP: dict[str, dict[str, float]] = {
    "半导体": {"semiconductor": 1.0, "tech": 0.8},
    "芯片": {"semiconductor": 0.9, "tech": 0.8},
    "集成电路": {"semiconductor": 1.0, "tech": 0.9},
    "AI": {"tech": 1.0, "growth": 0.7},
    "人工智能": {"tech": 1.0, "growth": 0.7},
    "ChatGPT": {"tech": 1.0, "growth": 0.6},
    "算力": {"tech": 1.0, "growth": 0.6},
    "大数据": {"tech": 0.9, "growth": 0.5},
    "云计算": {"tech": 1.0, "growth": 0.7},
    "信创": {"tech": 0.9, "defense": 0.3},
    "华为": {"tech": 0.8, "telecom": 0.6},
    "5G": {"tech": 0.7, "telecom": 0.9},
    "6G": {"tech": 0.7, "telecom": 1.0},
    "通信": {"telecom": 1.0, "tech": 0.6},
    "光纤": {"telecom": 0.9, "tech": 0.5},
    "CPO": {"tech": 0.8, "telecom": 0.7},
    "光通信": {"telecom": 0.9, "tech": 0.7},
    "新能源汽车": {"new_energy": 1.0, "growth": 0.7},
    "新能源车": {"new_energy": 1.0, "growth": 0.7},
    "锂电池": {"new_energy": 0.9, "growth": 0.6},
    "光伏": {"new_energy": 1.0, "growth": 0.7},
    "风电": {"new_energy": 0.9, "growth": 0.5},
    "储能": {"new_energy": 0.8, "growth": 0.6},
    "新能源": {"new_energy": 0.9, "growth": 0.5},
    "军工": {"defense": 1.0, "growth": 0.4},
    "航天": {"defense": 0.9, "growth": 0.4},
    "低空经济": {"defense": 0.6, "growth": 0.7, "tech": 0.4},
    "飞行汽车": {"defense": 0.5, "growth": 0.8, "tech": 0.5},
    "医疗": {"healthcare": 1.0, "growth": 0.5},
    "医药": {"healthcare": 1.0, "growth": 0.5},
    "创新药": {"healthcare": 1.0, "growth": 0.7},
    "医疗器械": {"healthcare": 1.0},
    "消费": {"consumer": 1.0},
    "白酒": {"consumer": 1.0, "liquor": 1.0, "value": 0.3},
    "食品": {"consumer": 1.0},
    "免税": {"consumer": 0.9},
    "旅游": {"consumer": 0.8},
    "游戏": {"tech": 0.8, "internet": 0.7, "consumer": 0.5},
    "互联网": {"internet": 1.0, "tech": 0.8},
    "黄金": {"commodity_gold": 1.0},
    "白银": {"commodity_gold": 0.9},
    "贵金属": {"commodity_gold": 0.9},
    "石油": {"commodity_oil": 1.0},
    "原油": {"commodity_oil": 1.0},
    "煤炭": {"commodity_energy": 0.8},
    "电力": {"commodity_energy": 0.7, "growth": 0.3},
    "超超临界": {"commodity_energy": 0.8},
    "创业板": {"growth": 0.8, "tech": 0.5, "broad_market": 0.4},
    "中证1000": {"broad_market": 0.8, "growth": 0.5},
    "纳指": {"us_equity": 1.0, "tech": 0.9},
    "纳斯达克": {"us_equity": 1.0, "tech": 0.9},
    "港股": {"hongkong_equity": 1.0},
    "恒生": {"hongkong_equity": 1.0},
    "日经": {"japan_equity": 1.0},
    "日本": {"japan_equity": 1.0},
    "豆粕": {"commodity_agriculture": 1.0},
    "猪肉": {"commodity_agriculture": 0.8, "consumer": 0.5},
    "农业": {"commodity_agriculture": 0.7, "consumer": 0.4},
    "金融": {"finance": 0.9, "value": 0.6},
    "券商": {"finance": 1.0, "growth": 0.5},
    "银行": {"finance": 1.0, "value": 0.8},
    "地产": {"real_estate": 1.0, "value": 0.5},
    "红利": {"dividend_value": 1.0, "value": 0.9},
    "高股息": {"dividend_value": 1.0, "value": 0.9},
}


# ── Private helpers ──


def _load_ths_hot_concepts(trade_date: str) -> list[str]:
    """Load ths_hot for a date and extract concept names from the raw column.

    Uses load_ths_hot from data_sources.platform for data-lake access,
    then applies the same concept-string extraction logic as the original.
    """
    df = load_ths_hot(trade_date, limit=100)
    if df.empty:
        return []

    # Sort by rank ascending and take top 30 (matching original behavior)
    if "rank" in df.columns:
        df = df.sort_values("rank").head(30)

    all_concepts: list[str] = []
    for raw in df["concept"]:
        s = str(raw).strip().strip("[]").strip('"').strip("'")
        parts = re.split(r"[\",，]\s*", s)
        for p in parts:
            p = p.strip().strip('"').strip("'")
            if p and p not in ("", "[", "]"):
                all_concepts.append(p)
    return all_concepts


def _score_etfs(concepts: list[str]) -> dict[str, float]:
    """Score all ETFs based on concept→exposure matching.

    Aggregates concept exposure weights into dimension scores,
    then computes the dot product with each ETF's exposure profile.
    """
    # Aggregate concept exposure scores
    dim_scores: dict[str, float] = {}
    for c in concepts:
        for keyword, dims in CONCEPT_EXPOSURE_MAP.items():
            if keyword.lower() in c.lower() or c.lower() in keyword.lower():
                for dim, weight in dims.items():
                    dim_scores[dim] = dim_scores.get(dim, 0) + weight

    if not dim_scores:
        return dict.fromkeys(ETF_METADATA, 0.0)

    # Score each ETF by dot product of its exposures with dim_scores
    scores: dict[str, float] = {}
    for sym, meta in ETF_METADATA.items():
        score = 0.0
        for dim, weight in meta["exposures"].items():
            if dim in dim_scores:
                score += dim_scores[dim] * weight
        scores[sym] = score

    return scores


def _load_etf_csv(symbol: str) -> pd.DataFrame | None:
    """Load an ETF CSV file from rotation-v3's data/raw/etf/."""
    path = ROTATION_ROOT / "data" / "raw" / "etf" / f"{symbol}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date")
    except Exception:
        return None


def _load_etf_data() -> dict[str, pd.DataFrame]:
    """Pre-load all ETF CSV data into a dict keyed by symbol."""
    etf_data: dict[str, pd.DataFrame] = {}
    for sym in ETF_METADATA:
        df = _load_etf_csv(sym)
        if df is not None:
            etf_data[sym] = df
    print(f"  Loaded {len(etf_data)} ETFs")
    return etf_data


def _build_date_list(start_date: str, end_date: str) -> list[str]:
    """Build a sorted list of YYYYMMDD trade-date strings within the range.

    Dates are discovered from the ths_hot data lake via the platform module.
    """
    from ..data_sources.platform import list_available_dates

    all_dates = list_available_dates("ths_hot")
    if not all_dates:
        return []

    s = start_date.replace("-", "")
    e = end_date.replace("-", "")
    dates = [d for d in all_dates if s <= d <= e]
    print(f"  Trading days: {len(dates)}")
    return dates


def _compute_daily_return(
    i: int,
    all_dates: list[str],
    top_k: int,
    fee_rate: float,
    etf_data: dict[str, pd.DataFrame],
) -> tuple[float | None, dict | None]:
    """Compute one day's portfolio return.

    Returns (portfolio_ret, trade_record) or (None, None) if no valid trade.
    """
    date_str = all_dates[i]
    dt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    # 1. Get today's hotspot concepts
    concepts = _load_ths_hot_concepts(dt)
    if not concepts:
        return None, None

    # 2. Score ETFs
    scores = _score_etfs(concepts)
    if not scores:
        return None, None

    # 3. Pick top K ETFs
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    selected = [(sym, sc) for sym, sc in ranked if sc > 0][:top_k]
    if not selected:
        return None, None

    # 4. Find next trading day entry price
    next_idx = i + 1
    if next_idx >= len(all_dates):
        return None, None
    entry_date_str = all_dates[next_idx]
    entry_dt = f"{entry_date_str[:4]}-{entry_date_str[4:6]}-{entry_date_str[6:]}"

    # 5. Calculate equal-weight return
    entry_prices: dict[str, float] = {}
    for sym, _ in selected:
        df = etf_data.get(sym)
        if df is None:
            continue
        row = df[df["date"] == entry_dt]
        if row.empty:
            continue
        entry_prices[sym] = float(row.iloc[0]["open"])

    if not entry_prices:
        return None, None

    # 6. Find exit price (next next day)
    exit_idx = next_idx + 1
    if exit_idx >= len(all_dates):
        return None, None
    exit_date_str = all_dates[exit_idx]
    exit_dt = f"{exit_date_str[:4]}-{exit_date_str[4:6]}-{exit_date_str[6:]}"

    portfolio_ret = 0.0
    valid_count = 0
    for sym, price_in in entry_prices.items():
        df = etf_data.get(sym)
        if df is None:
            continue
        exit_row = df[df["date"] == exit_dt]
        if exit_row.empty:
            continue
        price_out = float(exit_row.iloc[0]["open"])
        if price_in <= 0:
            continue
        ret = price_out / price_in - 1 - fee_rate * 2  # buy + sell fee
        portfolio_ret += ret
        valid_count += 1

    if valid_count == 0:
        return None, None
    portfolio_ret /= valid_count

    trade_record = {
        "date": entry_dt,
        "concepts": concepts[:5],
        "etfs": [s for s, _ in selected],
        "return_pct": round(portfolio_ret * 100, 2),
        # nav will be filled in by the caller
    }

    return portfolio_ret, trade_record


def _benchmark_csi300(all_dates: list[str], df_csi: pd.DataFrame | None) -> dict[str, float] | None:
    """Buy & hold 沪深300 ETF over the same trading period."""
    if df_csi is None or df_csi.empty:
        return None
    if len(all_dates) < 2:
        return None
    entry_date = f"{all_dates[1][:4]}-{all_dates[1][4:6]}-{all_dates[1][6:]}"
    exit_date = f"{all_dates[-1][:4]}-{all_dates[-1][4:6]}-{all_dates[-1][6:]}"
    entry = df_csi[df_csi["date"] == entry_date]
    exit_ = df_csi[df_csi["date"] == exit_date]
    if entry.empty or exit_.empty:
        return None
    entry_px = float(entry.iloc[0]["open"])
    exit_px = float(exit_.iloc[0]["close"])
    total = exit_px / entry_px - 1
    n_days = len(all_dates) - 1
    ann = float((1 + total) ** (252 / n_days) - 1) if n_days > 0 else 0
    return {"total_return": total, "annual_return": ann, "days": n_days}


def _build_result(
    daily_returns: list[float],
    trade_log: list[dict],
    nav: float,
    initial_capital: float,
    all_dates: list[str],
    etf_data: dict[str, pd.DataFrame],
    start_date: str,
    end_date: str,
    top_k: int,
    fee_rate: float,
) -> dict[str, Any]:
    """Assemble the final result dict from backtest outputs."""
    # Strategy metrics via shared compute_metrics
    strategy_metrics = compute_metrics(daily_returns, initial_capital)
    strategy_metrics["parameters"] = {"top_k": top_k, "fee_rate": fee_rate}

    # CSI 300 benchmark
    bm_data = None
    for sym in ("159919", "510300"):
        df = etf_data.get(sym)
        if df is not None and not df.empty:
            bm_data = df
            break
    bm = _benchmark_csi300(all_dates, bm_data)

    total_ret = strategy_metrics["total_return_pct"] / 100.0
    excess = total_ret - bm["total_return"] if bm else None

    # Per-year breakdown via shared yearly_breakdown
    yearly = yearly_breakdown(daily_returns, trade_log)

    # ETF selection frequency
    etf_counter: Counter[str] = Counter()
    for t in trade_log:
        for sym in t["etfs"]:
            etf_counter[sym] += 1
    top_etfs = dict(etf_counter.most_common(8))

    # Recent trades
    recent = trade_log[-10:] if len(trade_log) >= 10 else trade_log

    return {
        "period": f"{start_date} to {end_date}",
        "strategy": {
            "name": "Hotspot→ETF (ths_hot concepts → ETF exposures)",
            **strategy_metrics,
        },
        "benchmark": (
            {
                "label": "沪深300 ETF (159919/510300) 买入持有",
                "total_return_pct": round(bm["total_return"] * 100, 2),
                "annual_return_pct": round(bm["annual_return"] * 100, 2),
            }
            if bm
            else None
        ),
        "excess_return_pct": round(excess * 100, 2) if excess is not None else None,
        "yearly_breakdown": yearly,
        "most_selected_etfs": top_etfs,
        "recent_trades": recent[-10:] if recent else [],
    }


# ── Public API ──


def run_etf_backtest(
    top_k: int = 3,
    start_date: str = "2024-10-14",
    end_date: str = "2026-04-30",
    initial_capital: float = 1_000_000,
    fee_rate: float = 0.0005,
) -> dict[str, Any]:
    """Run hotspot-concept→ETF rotation backtest.

    Args:
        top_k: Number of top-scoring ETFs to hold each period.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        initial_capital: Starting capital.
        fee_rate: Bidirectional fee rate (applied at buy and sell).

    Returns:
        Dict with period, strategy, benchmark, excess_return_pct,
        yearly_breakdown, most_selected_etfs, recent_trades.
    """
    print(f"Hotspot→ETF Backtest ({start_date} ~ {end_date})")
    print(f"  {len(ETF_METADATA)} ETF metadata entries, top K={top_k}, fee={fee_rate * 100:.2f}%")
    print()

    # Pre-load all ETF data
    etf_data = _load_etf_data()

    # Build date list from ths_hot
    all_dates = _build_date_list(start_date, end_date)
    if not all_dates:
        return {"error": "No trading dates found in range"}

    # Backtest loop
    nav = initial_capital
    daily_returns: list[float] = []
    trade_log: list[dict] = []

    for i in range(len(all_dates)):
        ret, record = _compute_daily_return(i, all_dates, top_k, fee_rate, etf_data)
        if ret is None or record is None:
            continue

        nav *= 1 + ret
        daily_returns.append(ret)
        record["nav"] = round(nav, 0)
        trade_log.append(record)

        if len(trade_log) % 50 == 0:
            print(f"    ... {len(trade_log)} trades, NAV={nav:.0f}")

    # Assemble and return final result
    return _build_result(
        daily_returns=daily_returns,
        trade_log=trade_log,
        nav=nav,
        initial_capital=initial_capital,
        all_dates=all_dates,
        etf_data=etf_data,
        start_date=start_date,
        end_date=end_date,
        top_k=top_k,
        fee_rate=fee_rate,
    )
