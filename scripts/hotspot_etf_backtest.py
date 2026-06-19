"""
hotspot_etf_backtest.py — 热点概念→ETF 轮动回测

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

耗时：约 2 分钟（18只 ETF × 378 天读文件）
"""

from __future__ import annotations

import csv
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROTATION_ROOT = Path(os.environ.get("ETF_ROTATION_ROOT", "/home/richard/code/guan-etf-rotation-v3"))
DATA_ROOT = Path(os.environ.get("DATA_PLATFORM_ROOT", "/home/richard/data/market-data-platform"))

# ── ETF metadata (hardcoded from etf_metadata.yml) ──

ETF_METADATA: dict[str, dict] = {
    "162719": {"name": "广发道琼斯美国石油开发", "exposures": {"overseas_equity": 1.0, "us_equity": 1.0, "commodity_oil": 0.9}},
    "159915": {"name": "创业板ETF", "exposures": {"china_equity": 1.0, "growth": 1.0, "tech": 0.45, "broad_market": 0.4}},
    "159985": {"name": "豆粕ETF", "exposures": {"commodity_agriculture": 1.0}},
    "515030": {"name": "新能源车ETF", "exposures": {"china_equity": 1.0, "new_energy": 1.0, "growth": 0.8, "tech": 0.35}},
    "513060": {"name": "恒生医疗ETF", "exposures": {"hongkong_equity": 1.0, "healthcare": 1.0, "growth": 0.5}},
    "512560": {"name": "中证军工ETF", "exposures": {"china_equity": 1.0, "defense": 1.0, "growth": 0.4}},
    "516510": {"name": "云计算ETF", "exposures": {"china_equity": 1.0, "tech": 1.0, "growth": 0.8}},
    "515880": {"name": "通信ETF", "exposures": {"china_equity": 1.0, "tech": 0.85, "growth": 0.5, "telecom": 0.7}},
    "515790": {"name": "光伏ETF", "exposures": {"china_equity": 1.0, "new_energy": 1.0, "growth": 0.8}},
    "513100": {"name": "纳指ETF", "exposures": {"us_equity": 1.0, "tech": 0.75, "growth": 0.75}},
    "512100": {"name": "中证1000ETF", "exposures": {"china_equity": 1.0, "broad_market": 0.7, "growth": 0.35}},
    "512690": {"name": "酒ETF", "exposures": {"china_equity": 1.0, "consumer": 1.0, "value": 0.35, "liquor": 0.9}},
    "513330": {"name": "恒生互联网ETF", "exposures": {"hongkong_equity": 1.0, "tech": 0.85, "growth": 0.8, "internet": 0.9}},
    "518880": {"name": "黄金ETF", "exposures": {"commodity_gold": 1.0}},
    "161128": {"name": "标普科技LOF", "exposures": {"us_equity": 1.0, "tech": 1.0, "growth": 0.85}},
    "513300": {"name": "纳斯达克100ETF", "exposures": {"us_equity": 1.0, "tech": 0.75, "growth": 0.75}},
    "513310": {"name": "中韩半导体ETF", "exposures": {"semiconductor": 1.0, "tech": 1.0, "growth": 0.8}},
    "513880": {"name": "日经225ETF", "exposures": {"japan_equity": 1.0, "broad_market": 0.6}},
    # Additional ETFs from rotation-v3 full pool
    "515100": {"name": "红利100ETF", "exposures": {"dividend_value": 1.0, "value": 0.8, "china_equity": 0.8}},
    "515980": {"name": "人工智能ETF", "exposures": {"tech": 1.0, "growth": 0.8, "china_equity": 0.9}},
    "512480": {"name": "半导体ETF", "exposures": {"semiconductor": 1.0, "tech": 1.0, "growth": 0.7, "china_equity": 0.9}},
    "512980": {"name": "传媒ETF", "exposures": {"internet": 0.8, "consumer": 0.6, "tech": 0.6, "china_equity": 0.9}},
    "513690": {"name": "恒生高股息ETF", "exposures": {"hongkong_equity": 1.0, "dividend_value": 0.9, "value": 0.7}},
    "516640": {"name": "芯片ETF", "exposures": {"semiconductor": 1.0, "tech": 1.0, "growth": 0.7}},
    "561910": {"name": "电池ETF", "exposures": {"new_energy": 0.9, "growth": 0.7, "china_equity": 1.0}},
    "588000": {"name": "科创50ETF", "exposures": {"tech": 0.9, "growth": 0.9, "semiconductor": 0.5, "china_equity": 0.9}},
    "159329": {"name": "纳斯达克ETF", "exposures": {"us_equity": 1.0, "tech": 0.8, "growth": 0.7}},
    "159919": {"name": "沪深300ETF", "exposures": {"china_equity": 1.0, "broad_market": 1.0, "value": 0.5}},
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


def load_ths_hot_concepts(trade_date: str) -> list[str]:
    """Load ths_hot for a date and return concept frequency list."""
    d = DATA_ROOT / "assets" / "tushare" / "a_share" / "ths_hot"
    subdirs = sorted(p for p in d.iterdir() if p.is_dir())
    if not subdirs:
        return []
    data_dir = subdirs[-1] / "data"
    if not data_dir.is_dir():
        return []
    date_clean = trade_date.replace("-", "")
    part = data_dir / f"trade_date={date_clean}"
    if not part.is_dir():
        return []
    try:
        df = pd.read_parquet(part)
    except Exception:
        return []
    if df.empty:
        return []
    df = df.sort_values("rank").head(30)
    all_concepts: list[str] = []
    for raw in df["concept"]:
        s = str(raw).strip().strip("[]").strip('"').strip("'")
        parts = re.split(r'[",，]\s*', s)
        for p in parts:
            p = p.strip().strip('"').strip("'")
            if p and p not in ("", "[", "]"):
                all_concepts.append(p)
    return all_concepts


def score_etfs(concepts: list[str]) -> dict[str, float]:
    """Score all ETFs based on concept→exposure matching."""
    # Aggregate concept exposure scores
    dim_scores: dict[str, float] = {}
    for c in concepts:
        # Check if any keyword in CONCEPT_EXPOSURE_MAP matches this concept
        for keyword, dims in CONCEPT_EXPOSURE_MAP.items():
            if keyword.lower() in c.lower() or c.lower() in keyword.lower():
                for dim, weight in dims.items():
                    dim_scores[dim] = dim_scores.get(dim, 0) + weight

    if not dim_scores:
        return {sym: 0.0 for sym in ETF_METADATA}

    # Score each ETF by dot product of its exposures with dim_scores
    scores: dict[str, float] = {}
    max_raw = max(dim_scores.values())
    for sym, meta in ETF_METADATA.items():
        score = 0.0
        for dim, weight in meta["exposures"].items():
            if dim in dim_scores:
                score += dim_scores[dim] * weight
        scores[sym] = score

    return scores


def load_etf_csv(symbol: str) -> pd.DataFrame | None:
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


def run_hotspot_etf_backtest(
    top_k: int = 3,
    start_date: str = "2024-10-14",
    end_date: str = "2026-04-30",
    initial_capital: float = 1_000_000,
    fee_rate: float = 0.0005,
) -> dict:
    print(f"Hotspot→ETF Backtest ({start_date} ~ {end_date})")
    print(f"  18 ETFs, top K={top_k}, fee={fee_rate*100:.2f}%")
    print()

    # Pre-load all ETF data
    etf_data: dict[str, pd.DataFrame] = {}
    for sym in ETF_METADATA:
        df = load_etf_csv(sym)
        if df is not None:
            etf_data[sym] = df
    print(f"  Loaded {len(etf_data)} ETFs")

    # Build date index from the ths_hot directory
    d = DATA_ROOT / "assets" / "tushare" / "a_share" / "ths_hot"
    subdirs = sorted(p for p in d.iterdir() if p.is_dir())
    if not subdirs:
        return {"error": "ths_hot not found"}
    ths_data_dir = subdirs[-1] / "data"
    all_dates = sorted(
        e.name.split("=", 1)[1]
        for e in ths_data_dir.iterdir()
        if e.name.startswith("trade_date=")
    )
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")
    all_dates = [d_ for d_ in all_dates if s <= d_ <= e]
    print(f"  Trading days: {len(all_dates)}")

    # Backtest loop
    nav = initial_capital
    daily_returns: list[float] = []
    trade_days = 0
    hit_days = 0
    trade_log: list[dict] = []

    for i, date_str in enumerate(all_dates):
        dt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

        # 1. Get today's hotspot concepts
        concepts = load_ths_hot_concepts(dt)
        if not concepts:
            continue

        # 2. Score ETFs
        scores = score_etfs(concepts)
        if not scores:
            continue

        # 3. Pick top K ETFs
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        selected = [(sym, sc) for sym, sc in ranked if sc > 0][:top_k]
        if not selected:
            continue

        # 4. Find next trading day entry price
        next_idx = i + 1
        if next_idx >= len(all_dates):
            continue
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
            continue

        # 6. Find exit price (next next day)
        exit_idx = next_idx + 1
        if exit_idx >= len(all_dates):
            continue
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
            continue
        portfolio_ret /= valid_count

        nav *= (1 + portfolio_ret)
        daily_returns.append(portfolio_ret)
        trade_days += 1
        if portfolio_ret > 0:
            hit_days += 1

        trade_log.append({
            "date": entry_dt,
            "concepts": concepts[:5],
            "etfs": [s for s, _ in selected],
            "return_pct": round(portfolio_ret * 100, 2),
            "nav": round(nav, 0),
        })

        if trade_days % 50 == 0:
            print(f"    ... {trade_days} trades, NAV={nav:.0f}")

    # ── compute metrics ──
    ret_arr = np.array(daily_returns)
    n = len(ret_arr)
    total = float(np.prod(1 + ret_arr) - 1) if n > 0 else 0
    ann = float((1 + total) ** (252 / n) - 1) if n > 0 else 0
    vol = float(np.std(ret_arr) * np.sqrt(252)) if n > 1 else 0
    sharpe = float(ann / vol) if vol > 0 else 0
    hit_rate = float(hit_days / n) if n > 0 else 0
    max_dd = float(_max_drawdown(ret_arr))

    # CSI 300 benchmark — use 159919 (沪深300ETF) or 510300
    bm_data = None
    for sym in ("159919", "510300"):
        df = etf_data.get(sym)
        if df is not None and not df.empty:
            bm_data = df
            break
    bm = _benchmark_csi300(all_dates, bm_data)
    excess = total - bm["total_return"] if bm else None

    # Per-year breakdown
    yearly = _yearly_breakdown(daily_returns, trade_log)

    # Recent trades
    recent = trade_log[-10:] if len(trade_log) >= 10 else trade_log

    # ETF selection frequency
    from collections import Counter
    etf_counter: Counter = Counter()
    for t in trade_log:
        for sym in t["etfs"]:
            etf_counter[sym] += 1
    top_etfs = {sym: cnt for sym, cnt in etf_counter.most_common(8)}

    return {
        "period": f"{start_date} to {end_date}",
        "strategy": {
            "name": "Hotspot→ETF (ths_hot concepts → ETF exposures)",
            "total_return_pct": round(total * 100, 2),
            "annual_return_pct": round(ann * 100, 2),
            "annual_vol_pct": round(vol * 100, 2),
            "sharpe": round(sharpe, 3),
            "hit_rate_pct": round(hit_rate * 100, 1),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "trade_days": n,
            "final_nav": round(nav, 0),
            "parameters": {"top_k": top_k, "fee_rate": fee_rate},
        },
        "benchmark": {
            "label": "沪深300 ETF (159919/510300) 买入持有",
            "total_return_pct": round(bm["total_return"] * 100, 2) if bm else None,
            "annual_return_pct": round(bm["annual_return"] * 100, 2) if bm else None,
        } if bm else None,
        "excess_return_pct": round(excess * 100, 2) if excess is not None else None,
        "yearly_breakdown": yearly,
        "most_selected_etfs": top_etfs,
        "recent_trades": recent[-10:] if recent else [],
    }


def _max_drawdown(returns: np.ndarray) -> float:
    if len(returns) == 0:
        return 0.0
    cum = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    return float(np.min(dd))


def _benchmark_csi300(all_dates: list[str], df_csi: pd.DataFrame | None) -> dict | None:
    """Buy & hold 沪深300 ETF over the same trading period."""
    if df_csi is None or df_csi.empty:
        return None
    # Entry: second date (strategy enters next day)
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


def _yearly_breakdown(
    daily_returns: list[float], trade_log: list[dict]
) -> list[dict]:
    """Compute per-calendar-year performance."""
    if not trade_log:
        return []
    by_year: dict[str, list[float]] = {}
    for t, ret in zip(trade_log, daily_returns):
        year = t["date"][:4]
        by_year.setdefault(year, []).append(ret)
    result = []
    for year in sorted(by_year):
        arr = np.array(by_year[year])
        n = len(arr)
        total = float(np.prod(1 + arr) - 1)
        ann = float((1 + total) ** (252 / n) - 1) if n > 0 else 0
        vol = float(np.std(arr) * np.sqrt(252)) if n > 1 else 0
        sharpe = float(ann / vol) if vol > 0 else 0
        hit = float(np.mean(arr > 0)) * 100
        dd = float(_max_drawdown(arr))
        result.append({
            "year": year,
            "trades": n,
            "return_pct": round(total * 100, 2),
            "sharpe": round(sharpe, 3),
            "hit_rate_pct": round(hit, 1),
            "max_dd_pct": round(dd * 100, 2),
        })
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--start", default="2024-10-14")
    parser.add_argument("--end", default="2026-04-30")
    parser.add_argument("--fee", type=float, default=0.0005)
    args = parser.parse_args()

    result = run_hotspot_etf_backtest(
        top_k=args.top_k,
        start_date=args.start,
        end_date=args.end,
        fee_rate=args.fee,
    )

    import json
    print("\n" + "=" * 70)
    print(json.dumps(result, ensure_ascii=False, indent=2))
