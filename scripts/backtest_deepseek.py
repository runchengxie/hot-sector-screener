#!/usr/bin/env python3
"""End-to-end backtest pipeline: hot-sector candidates → DeepSeek picks → backtest.

Phases (run independently or together)::

    # Phase 1: Generate candidate manifests for a date range (no LLM, free)
    uv run python scripts/backtest_deepseek.py --phase candidates --start 20260601 --end 20260714

    # Phase 2: DeepSeek picks from each manifest
    uv run python scripts/backtest_deepseek.py --phase picks --start 20260601 --end 20260714

    # Phase 3: Aggregate and run backtest via portfolio-backtester
    uv run python scripts/backtest_deepseek.py --phase backtest --start 20260601 --end 20260714

    # All three phases (one shot)
    uv run python scripts/backtest_deepseek.py --phase all --lookback 30

Environment::

    DATA_PLATFORM_ROOT   Path to market-data-platform data lake (required)
    DEEPSEEK_API_KEY     DeepSeek API key (required for phase picks)
    LLM_API_URL          Override API base URL
    LLM_MODEL            Override model (default: deepseek-chat)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
DATA_PLATFORM_ROOT = Path(
    os.environ.get("DATA_PLATFORM_ROOT", "/home/richard/data/market-data-platform")
)
DAILY_PRICE_DIR = (
    DATA_PLATFORM_ROOT
    / "assets/tushare/a_share/daily_clean_inputs"
    / "a_share_all_20150101_20260714/daily/data"
)

# portfolio-backtester location
PORTFOLIO_BACKTESTER_ROOT = Path(
    os.environ.get(
        "PORTFOLIO_BACKTESTER_ROOT",
        str(Path.home() / "code/research-workspace/portfolio-backtester"),
    )
)


# ---------------------------------------------------------------------------
# Trading calendar
# ---------------------------------------------------------------------------
def load_trade_dates(start: str, end: str) -> list[str]:
    """Return sorted YYYYMMDD trade dates in [start, end] that exist in ths_hot."""
    ths_dir = DATA_PLATFORM_ROOT / "assets/tushare/a_share/ths_hot/a_share_all_ths_hot_latest/data"
    if not ths_dir.exists():
        print(f"WARNING: {ths_dir} not found, returning all dates in range", file=sys.stderr)
        # fallback: generate all weekdays
        dates: list[str] = []
        d = datetime.strptime(start, "%Y%m%d")
        d_end = datetime.strptime(end, "%Y%m%d")
        while d <= d_end:
            if d.weekday() < 5:
                dates.append(d.strftime("%Y%m%d"))
            d += timedelta(days=1)
        return dates

    available = set()
    for entry in ths_dir.iterdir():
        if entry.is_dir() and entry.name.startswith("trade_date="):
            available.add(entry.name.removeprefix("trade_date="))

    dates: list[str] = []
    d = datetime.strptime(start, "%Y%m%d")
    d_end = datetime.strptime(end, "%Y%m%d")
    while d <= d_end:
        ds = d.strftime("%Y%m%d")
        if ds in available:
            dates.append(ds)
        d += timedelta(days=1)
    return sorted(dates)


# ---------------------------------------------------------------------------
# Phase 1: Candidate generation
# ---------------------------------------------------------------------------
def run_phase_candidates(dates: list[str], top_n: int) -> dict[str, Path]:
    """Generate candidate_universe.csv for each date via hotsector (no LLM)."""
    from hot_sector_screener.config import load_config
    from hot_sector_screener.universe_builder import Screener

    config_path = PROJECT_ROOT / "configs" / "default.yml"
    config = load_config(str(config_path))
    config.setdefault("llm", {})["enabled"] = False
    if "universe" not in config:
        config["universe"] = {}
    config["universe"]["max_candidates"] = top_n

    builder = Screener(config)
    manifests: dict[str, Path] = {}

    for i, date in enumerate(dates):
        out_dir = OUTPUTS_DIR / date
        csv_path = out_dir / "candidate_universe.csv"
        if csv_path.exists():
            print(f"  [{i + 1}/{len(dates)}] {date} — already exists, skip")
            manifests[date] = csv_path
            continue

        print(f"  [{i + 1}/{len(dates)}] {date} — generating candidates...")
        result = builder.build_universe(trade_date=date, output_dir=out_dir)
        n = result.get("universe_size", 0)
        print(f"           → {n} candidates")
        manifests[date] = out_dir / "candidate_universe.csv"

    return manifests


# ---------------------------------------------------------------------------
# Phase 2: DeepSeek picks
# ---------------------------------------------------------------------------
def run_phase_picks(dates: list[str], top_n: int, model: str | None) -> list[Path]:
    """Run deepseek_pick.py for each date that has a candidate_universe.csv."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "deepseek_pick", PROJECT_ROOT / "scripts" / "deepseek_pick.py"
    )
    dsp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dsp)

    pick_files: list[Path] = []

    for i, date in enumerate(dates):
        out_dir = OUTPUTS_DIR / date
        csv_path = out_dir / "candidate_universe.csv"
        json_path = out_dir / "deepseek_picks.json"

        if json_path.exists():
            print(f"  [{i + 1}/{len(dates)}] {date} — picks already exist, skip")
            pick_files.append(json_path)
            continue

        if not csv_path.exists():
            print(f"  [{i + 1}/{len(dates)}] {date} — no candidate CSV, skip")
            continue

        df = pd.read_csv(csv_path)
        if "score" in df.columns:
            df = df.sort_values("score", ascending=False)

        prompt = dsp.build_stock_pick_prompt(df, date, top_n=top_n)

        print(f"  [{i + 1}/{len(dates)}] {date} — calling DeepSeek ({len(df)} candidates)...")
        try:
            response = dsp.call_deepseek(prompt, model=model)
        except RuntimeError as e:
            print(f"           ERROR: {e}", file=sys.stderr)
            continue

        picks = dsp.parse_pick_response(response)
        if not picks:
            print(f"           WARNING: could not parse picks, raw: {response[:120]}")
            continue

        result = {
            "run_date": date,
            "generated_at": datetime.now().isoformat(),
            "model": model or os.environ.get("LLM_MODEL", "deepseek-chat"),
            "top_n": top_n,
            "n_candidates": len(df),
            "picks": picks,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"           → {len(picks)} picks saved")
        pick_files.append(json_path)

    return pick_files


# ---------------------------------------------------------------------------
# Phase 3: Backtest via portfolio-backtester
# ---------------------------------------------------------------------------
def run_phase_backtest(  # noqa: C901
    dates: list[str], pick_files: list[Path], top_n: int
) -> dict[str, Any]:
    """Backtest DeepSeek picks vs candidate-pool baseline using portfolio-backtester."""
    import numpy as np

    sys.path.insert(0, str(PORTFOLIO_BACKTESTER_ROOT / "src"))
    from portfolio_backtester.position_backtest import PositionBacktestConfig, run_position_backtest

    # --- Load pricing for all symbols across all dates ---
    all_symbols: set[str] = set()
    picks_index: dict[str, list[dict]] = {}  # date → [{symbol, conf}]
    pool_index: dict[str, list[str]] = {}  # date → [symbol, ...]

    for date, pf in zip(dates, pick_files, strict=True):
        if not pf.exists():
            continue
        data = json.loads(pf.read_text())
        picks_index[date] = [
            {"symbol": p["ts_code"], "conf": p["confidence_score"]} for p in data["picks"]
        ]
        for p in data["picks"]:
            all_symbols.add(p["ts_code"])

        # Also load candidate pool
        csv_path = OUTPUTS_DIR / date / "candidate_universe.csv"
        if csv_path.exists():
            pool_df = pd.read_csv(csv_path)
            pool_index[date] = list(pool_df["ts_code"])
            all_symbols.update(pool_df["ts_code"])

    if not picks_index:
        raise RuntimeError("No picks found — nothing to backtest.")

    # --- Load pricing table ---
    pricing_rows = []
    for ds in sorted(set(dates)):
        part_dir = DAILY_PRICE_DIR / f"trade_date={ds}"
        if not part_dir.exists():
            continue
        for f in part_dir.iterdir():
            if f.suffix == ".parquet":
                pdf = pd.read_parquet(f, columns=["ts_code", "trade_date", "close"])
                pdf = pdf.rename(columns={"ts_code": "symbol"})
                pdf["trade_date"] = pd.to_datetime(pdf["trade_date"])
                pdf = pdf[pdf["symbol"].isin(all_symbols)]
                pricing_rows.append(pdf[["trade_date", "symbol", "close"]])
                break
    pricing = pd.concat(pricing_rows, ignore_index=True)
    all_trade_dates = sorted(pricing["trade_date"].unique())

    # --- Build positions (strategy: top-N picks, equal weight) ---
    pos_rows = []
    for date_str in sorted(picks_index.keys()):
        sig_date = pd.Timestamp(datetime.strptime(date_str, "%Y%m%d"))
        picks = sorted(picks_index[date_str], key=lambda x: -x["conf"])[:top_n]
        w = 1.0 / len(picks) if picks else 0
        for p in picks:
            pos_rows.append({"rebalance_date": sig_date, "symbol": p["symbol"], "weight": w})
    positions = pd.DataFrame(pos_rows)

    # --- Build periods ---
    sorted_dates = sorted(positions["rebalance_date"].unique())
    period_rows = []
    for i, sig_date in enumerate(sorted_dates[:-1]):
        entry_date = all_trade_dates[i + 1] if i + 1 < len(all_trade_dates) else sig_date
        exit_date = all_trade_dates[i + 2] if i + 2 < len(all_trade_dates) else entry_date
        period_rows.append(
            {
                "rebalance_date": sig_date,
                "entry_date": entry_date,
                "exit_date": exit_date,
            }
        )
    periods = pd.DataFrame(period_rows)

    config = PositionBacktestConfig(
        price_col="close",
        transaction_cost_bps=10,
        trading_days_per_year=252,
    )

    # --- Strategy backtest ---
    print(f"\nStrategy: {len(positions)} positions, {positions.rebalance_date.nunique()} dates")
    result = run_position_backtest(
        positions=positions, pricing=pricing, periods=periods, config=config
    )
    s = result.summary["stats"]

    # --- Baseline backtest (all candidates, equal weight) ---
    base_rows = []
    for date_str in sorted(pool_index.keys()):
        sig_date = pd.Timestamp(datetime.strptime(date_str, "%Y%m%d"))
        syms = pool_index[date_str]
        if not syms:
            continue
        w = 1.0 / len(syms)
        for sym in syms:
            base_rows.append({"rebalance_date": sig_date, "symbol": sym, "weight": w})
    base_positions = pd.DataFrame(base_rows)

    print(
        f"Baseline: {len(base_positions)} positions,"
        f" {base_positions.rebalance_date.nunique()} dates"
    )
    base_result = run_position_backtest(
        positions=base_positions, pricing=pricing, periods=periods, config=config
    )
    bs = base_result.summary["stats"]

    # --- Print comparison ---
    print()
    print("=" * 65)
    print(f"{'Metric':<25s} {'Strategy':>14s} {'Baseline':>14s}")
    print("=" * 65)
    for key, label in [
        ("periods", "Periods"),
        ("total_return", "Total Return"),
        ("ann_return", "Ann. Return"),
        ("ann_vol", "Ann. Vol"),
        ("sharpe", "Sharpe"),
        ("max_drawdown", "Max Drawdown"),
        ("avg_turnover", "Avg Turnover"),
    ]:
        sv = s.get(key)
        bv = bs.get(key)
        if isinstance(sv, (int, float, np.floating)) and isinstance(bv, (int, float, np.floating)):
            if key in ("total_return", "ann_return", "ann_vol", "max_drawdown", "avg_turnover"):
                print(f"{label:<25s} {float(sv) * 100:>13.2f}% {float(bv) * 100:>13.2f}%")
            else:
                print(f"{label:<25s} {float(sv):>14.3f} {float(bv):>14.3f}")
        else:
            print(f"{label:<25s} {sv!s:>14s} {bs!s:>14s}")
    print("=" * 65)

    alpha = float(s["total_return"]) - float(bs["total_return"])
    print(f"\nDeepSeek Alpha (excess over baseline): {alpha * 100:+.2f}%")

    # Save
    out = OUTPUTS_DIR / "deepseek_backtest_result.json"
    summary = {
        "engine": "portfolio-backtester run_position_backtest",
        "config": {"cost_bps": 10, "top_n": top_n},
        "strategy": {k: float(v) if isinstance(v, (np.floating,)) else v for k, v in s.items()},
        "baseline": {k: float(v) if isinstance(v, (np.floating,)) else v for k, v in bs.items()},
    }
    with open(out, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Saved: {out}")

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Hot-sector + DeepSeek picks → backtest pipeline")
    parser.add_argument(
        "--phase",
        choices=["candidates", "picks", "backtest", "all"],
        default="all",
        help="Which phase(s) to run",
    )
    parser.add_argument("--start", help="Start date YYYYMMDD (default: 30 days ago)")
    parser.add_argument("--end", help="End date YYYYMMDD (default: today)")
    parser.add_argument(
        "--lookback",
        type=int,
        default=30,
        help="Days to look back from --end (default: 30)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of stocks to pick per day (default: 10)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="DeepSeek model (default: deepseek-chat)",
    )
    parser.add_argument(
        "--candidate-pool-size",
        type=int,
        default=100,
        help="Max candidates per day from hotsector (default: 100)",
    )
    args = parser.parse_args()

    # Date range
    end_date = args.end or datetime.now().strftime("%Y%m%d")
    start_date = args.start or (
        datetime.strptime(end_date, "%Y%m%d") - timedelta(days=args.lookback)
    ).strftime("%Y%m%d")

    trade_dates = load_trade_dates(start_date, end_date)
    if not trade_dates:
        print(f"ERROR: no trade dates found in [{start_date}, {end_date}]", file=sys.stderr)
        sys.exit(1)

    print(f"Pipeline: {start_date} → {end_date} ({len(trade_dates)} trade dates)")
    print(f"Model: {args.model or os.environ.get('LLM_MODEL', 'deepseek-chat')}")
    print(f"Top-N per day: {args.top_n}")
    print()

    # --- Phase 1: Candidates ---
    if args.phase in ("candidates", "all"):
        print("=" * 60)
        print("PHASE 1: Candidate generation (--no-llm)")
        print("=" * 60)
        run_phase_candidates(trade_dates, top_n=args.candidate_pool_size)
        print()

    # --- Phase 2: Picks ---
    pick_files: list[Path] = []
    if args.phase in ("picks", "all"):
        print("=" * 60)
        print("PHASE 2: DeepSeek stock picking")
        print("=" * 60)
        pick_files = run_phase_picks(trade_dates, top_n=args.top_n, model=args.model)
        print()
    else:
        # Reconstruct pick file paths for backtest phase
        pick_files = [OUTPUTS_DIR / d / "deepseek_picks.json" for d in trade_dates]

    # --- Phase 3: Backtest ---
    if args.phase in ("backtest", "all"):
        print("=" * 60)
        print("PHASE 3: Backtest")
        print("=" * 60)
        result = run_phase_backtest(trade_dates, pick_files, top_n=args.top_n)
        print(f"\nBacktest result: {json.dumps(result, indent=2, default=str)}")


if __name__ == "__main__":
    main()
