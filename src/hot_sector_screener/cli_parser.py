"""Argument parser construction for the `hotsector` CLI.

Split out of `cli.py` so the parser builder stays in a focused module and the
main CLI file is easier to read. `build_parser()` is re-exported from `cli.py`.
"""

from __future__ import annotations

import argparse
from typing import Any

from .candidate_contract import CANDIDATE_FEATURE_SET_ID, CANDIDATE_MODEL_ID
from .production_quality import DEFAULT_REQUIRED_SOURCES


def _add_core_parsers(sub: Any) -> None:
    """Register the non-backtest subcommands."""

    # info — show data coverage
    info = sub.add_parser("info", help="Show available hotspot data in data lake")
    info.add_argument("--source", default=None, help="Filter by source name")

    # latest-date — resolve the most recent date where required sources overlap
    latest = sub.add_parser("latest-date", help="Print latest common trade date")
    latest.add_argument(
        "--sources",
        default=None,
        help=("Comma-separated source list. Default: " + ",".join(DEFAULT_REQUIRED_SOURCES)),
    )
    latest.add_argument("--json", action="store_true", help="Print JSON payload")

    # scan — collect data without LLM
    scan = sub.add_parser("scan", help="Collect hotspot data (no LLM call)")
    scan.add_argument("--date", default=None, help="Trade date (YYYY-MM-DD or YYYYMMDD)")
    scan.add_argument("--config", default=None, help="Config YAML path")

    # run — full pipeline with LLM
    run = sub.add_parser("run", help="Full pipeline: collect → LLM → map → universe")
    run.add_argument("--date", default=None, help="Trade date (YYYY-MM-DD or YYYYMMDD)")
    run.add_argument("--config", default=None, help="Config YAML path")
    run.add_argument(
        "--no-llm",
        action="store_true",
        help="Explicitly skip LLM and use deterministic topic extraction",
    )
    run.add_argument("--output-dir", default=None, help="Custom output directory")
    run.add_argument("--max-candidates", type=int, default=None, help="Override max candidates")
    run.add_argument("--stocks-per-topic", type=int, default=None, help="Override stocks per topic")
    run.add_argument(
        "--load-topics",
        default=None,
        help="Path to topics JSON file (skip LLM, use pre-classified topics)",
    )
    run.add_argument(
        "--holdings",
        default=None,
        help="Versioned holdings snapshot JSON for the daily eligibility overlay",
    )

    # universe — list latest or specific output
    universe = sub.add_parser("universe", help="Show candidate universe output")
    universe.add_argument("--date", default=None, help="Output date to show")
    universe.add_argument("--csv", action="store_true", help="Output as CSV")
    universe.add_argument("--limit", type=int, default=30, help="Max stocks to display")

    # build-prompt — collect data and write LLM prompt to file (no LLM call)
    bp = sub.add_parser("build-prompt", help="Collect hotspot data and write LLM prompt to file")
    bp.add_argument("--date", default=None, help="Trade date (YYYY-MM-DD or YYYYMMDD)")
    bp.add_argument("--config", default=None, help="Config YAML path")
    bp.add_argument("--out-prompt", default="hotspot_prompt.txt", help="Output prompt file path")
    bp.add_argument("--stock-limit", type=int, default=30, help="Max hot stocks in prompt")
    bp.add_argument("--concept-limit", type=int, default=20, help="Max concepts in prompt")

    # export-signals — convert candidate universe into a standard signal artifact
    es = sub.add_parser(
        "export-signals",
        help="Export candidate universe as alpha-research signals.parquet",
    )
    es.add_argument("--date", default=None, help="Output date to export")
    es.add_argument("--input", default=None, help="candidate_universe.json path")
    es.add_argument("--output-dir", default=None, help="Signal output directory")
    es.add_argument("--model-version", default=CANDIDATE_MODEL_ID)
    es.add_argument("--feature-set-id", default=CANDIDATE_FEATURE_SET_ID)

    # validate-output — production gate for scheduled handoff jobs
    vo = sub.add_parser(
        "validate-output",
        help="Validate one output directory for scheduled candidate-signal production",
    )
    vo.add_argument("--date", default=None, help="Output date to validate")
    vo.add_argument("--output-dir", default=None, help="Output directory to validate")
    vo.add_argument(
        "--require-sources",
        default=None,
        help=(
            "Additional comma-separated fixed sources. Default: capability gate "
            "(normal/dc_fallback/event_fallback)"
        ),
    )
    vo.add_argument(
        "--min-candidates",
        type=int,
        default=None,
        help="Override min candidate count; default reads config_snapshot.min_candidates",
    )
    vo.add_argument(
        "--no-require-signals",
        action="store_true",
        help="Do not require non-empty signals.parquet/signals.meta.json",
    )

    holdings_validator = sub.add_parser(
        "validate-holdings-overlay",
        help="Validate an owner holdings overlay and print its canonical summary",
    )
    holdings_validator.add_argument(
        "--input",
        required=True,
        help="holdings_eligibility_overlay.json path",
    )


def _add_backtest_parsers(sub: Any) -> None:
    """Register the `backtest` command tree."""

    bt = sub.add_parser("backtest", help="Run hotspot-driven strategy backtests")
    bt_sub = bt.add_subparsers(dest="bt_command", required=True)

    # backtest stock
    bt_stock = bt_sub.add_parser("stock", help="Hotspot concept → stocks backtest")
    bt_stock.add_argument("--start", default="2024-10-14", help="Start date (YYYY-MM-DD)")
    bt_stock.add_argument("--end", default="2026-05-01", help="End date (YYYY-MM-DD)")
    bt_stock.add_argument("--top-concepts", type=int, default=3, help="Top N concepts per day")
    bt_stock.add_argument("--stocks-per-concept", type=int, default=10)
    bt_stock.add_argument("--sample", type=int, default=3, help="Sample every N trading days")
    bt_stock.add_argument("--capital", type=float, default=1_000_000, help="Initial capital")

    # backtest etf
    bt_etf = bt_sub.add_parser("etf", help="Hotspot concept → ETF rotation backtest")
    bt_etf.add_argument("--start", default="2024-10-14", help="Start date (YYYY-MM-DD)")
    bt_etf.add_argument("--end", default="2026-04-30", help="End date (YYYY-MM-DD)")
    bt_etf.add_argument("--top-k", type=int, default=3, help="Top K ETFs to hold")
    bt_etf.add_argument("--fee", type=float, default=0.0005, help="Fee rate per side")
    bt_etf.add_argument("--capital", type=float, default=1_000_000, help="Initial capital")

    # backtest etf-ml
    bt_etf_ml = bt_sub.add_parser(
        "etf-ml",
        help=(
            "ML-enhanced hotspot → ETF rotation backtest "
            "(with technical features + walk-forward training)"
        ),
    )
    bt_etf_ml.add_argument("--start", default="2024-10-14", help="Start date (YYYY-MM-DD)")
    bt_etf_ml.add_argument("--end", default="2026-04-30", help="End date (YYYY-MM-DD)")
    bt_etf_ml.add_argument("--top-k", type=int, default=3, help="Top K ETFs to hold")
    bt_etf_ml.add_argument("--fee", type=float, default=0.0005, help="Fee rate per side")
    bt_etf_ml.add_argument("--capital", type=float, default=1_000_000, help="Initial capital")
    bt_etf_ml.add_argument(
        "--model",
        default="linear_rank",
        choices=["linear_rank", "lightgbm_regression"],
        help="Model type",
    )
    bt_etf_ml.add_argument(
        "--step-days", type=int, default=40, help="Walk-forward step size in trading days"
    )
    bt_etf_ml.add_argument(
        "--min-train", type=int, default=120, help="Minimum training days before first fold"
    )
    bt_etf_ml.add_argument("--trials", type=int, default=10, help="Effective trials for DSR")


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level `hotsector` argument parser."""
    parser = argparse.ArgumentParser(prog="hotsector")
    sub = parser.add_subparsers(dest="command", required=True)

    _add_core_parsers(sub)
    _add_backtest_parsers(sub)

    return parser
