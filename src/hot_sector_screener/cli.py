from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backtest.etf_backtest import run_etf_backtest
from .backtest.etf_ml_backtest import run_etf_ml_backtest
from .backtest.stock_backtest import run_stock_backtest
from .candidate_contract import CandidateContractError
from .cli_parser import build_parser
from .config import default_config, load_config
from .data_sources.platform import summarize_data_coverage
from .holdings_contract import (
    HoldingsOverlayContractError,
    canonical_sha256,
    load_holdings_snapshot,
    validate_holdings_overlay,
)
from .observation_time import date_key, resolve_observation_date
from .paths import OUTPUTS_DIR
from .production_quality import parse_source_list, validate_candidate_output
from .signal_export import load_candidate_result, write_signal_artifacts
from .topic_classifier import TopicClassificationError, TopicValidationError
from .universe_builder import Screener


def cmd_info(args: argparse.Namespace) -> None:
    """Show available hotspot data coverage."""
    coverage = summarize_data_coverage()
    print(f"{'=' * 60}")
    print("  热点数据湖覆盖情况")
    print(f"{'=' * 60}")
    for source, info in coverage.items():
        print(f"\n  {source}:")
        print(f"    可用交易日: {info['available_dates']}")
        print(f"    最早:       {info['earliest']}")
        print(f"    最晚:       {info['latest']}")
        if info["sample_dates"]:
            print(f"    样本日期:   {', '.join(str(d) for d in info['sample_dates'])}")


def cmd_latest_date(args: argparse.Namespace) -> None:
    """Print latest common date across required sources."""
    sources = parse_source_list(args.sources)
    try:
        latest = resolve_observation_date(None, sources=sources)
    except RuntimeError:
        print(
            "No common trade date for sources: " + ",".join(sources),
            file=sys.stderr,
        )
        sys.exit(1)
    if args.json:
        print(json.dumps({"date": latest, "sources": list(sources)}, ensure_ascii=False))
    else:
        print(latest)


def cmd_scan(args: argparse.Namespace) -> None:
    """Collect hotspot data without LLM."""
    config = _resolve_config(args.config)
    builder = Screener(config)
    result = builder.scan(trade_date=args.date)

    print(f"\n  扫描日期: {result['date']}")
    print(f"  {'=' * 40}")
    for source, info in result.items():
        if source == "date":
            continue
        if isinstance(info, dict) and "rows" in info:
            status = "OK" if info["rows"] > 0 else "EMPTY"
            print(f"  {source:25s} rows={info['rows']:<6d} [{status}]")

    # Print sample stocks
    ths = result.get("ths_hot", {}).get("sample", [])
    if ths:
        print(f"\n  同花顺热榜 Top {len(ths)}:")
        print(f"  {'排名':>4s} {'代码':<12s} {'名称':<10s} {'热度':>6s} {'概念'}")
        print(f"  {'-' * 60}")
        for s in ths:
            print(
                f"  {s.get('rank', '')!s:>4s} {s.get('ts_code', '')!s:<12s} "
                f"{s.get('ts_name', '')!s:<10s} {s.get('hot', '')!s:>6s} "
                f"{s.get('concept', '')[:30]}"
            )


def _load_run_holdings(args: argparse.Namespace) -> dict | None:
    holdings_path = getattr(args, "holdings", None)
    if not holdings_path:
        return None
    try:
        observation_date = resolve_observation_date(args.date)
        return load_holdings_snapshot(
            holdings_path,
            observation_date=observation_date,
        )
    except (HoldingsOverlayContractError, RuntimeError, ValueError) as exc:
        print(f"ERROR: invalid holdings snapshot: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_run(args: argparse.Namespace) -> None:
    """Full pipeline run."""
    try:
        config = _resolve_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: invalid config: {exc}", file=sys.stderr)
        sys.exit(1)

    # CLI overrides
    if args.no_llm:
        config.setdefault("llm", {})["enabled"] = False
    if args.max_candidates is not None:
        config.setdefault("universe", {})["max_candidates"] = args.max_candidates
    if args.stocks_per_topic is not None:
        config.setdefault("universe", {})["stocks_per_topic"] = args.stocks_per_topic

    try:
        builder = Screener(config)
    except ValueError as exc:
        print(f"ERROR: invalid config: {exc}", file=sys.stderr)
        sys.exit(1)

    # Load pre-classified topics if --load-topics given
    pre_classified = None
    if args.load_topics:
        topics_path = Path(args.load_topics)
        if not topics_path.exists():
            print(f"ERROR: topics file not found: {topics_path}")
            sys.exit(1)
        try:
            with open(topics_path) as f:
                pre_classified = json.load(f)
        except json.JSONDecodeError as exc:
            print(f"ERROR: invalid topics JSON: {exc}", file=sys.stderr)
            sys.exit(1)

    holdings_snapshot = _load_run_holdings(args)

    try:
        result = builder.build_universe(
            trade_date=args.date,
            output_dir=args.output_dir,
            topics=pre_classified,
            holdings_snapshot=holdings_snapshot,
        )
    except TopicValidationError as exc:
        print(f"ERROR: invalid topics: {exc}", file=sys.stderr)
        sys.exit(1)
    except TopicClassificationError as exc:
        print(f"ERROR: topic classification failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except HoldingsOverlayContractError as exc:
        print(f"ERROR: holdings overlay failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  运行日期: {result['date']}")
    print(f"  生成时间: {result['generated_at']}")
    print(f"  {'=' * 40}")

    # Topics
    print(f"\n  观测日主题空间 ({len(result['topics'])} 个主题):")
    print(f"  {'主题':<30s} {'权重':>6s} {'来源'}")
    print(f"  {'-' * 60}")
    for t in result["topics"]:
        sources = ", ".join(t.get("source_signals", []))
        print(f"  {t.get('topic', ''):<30s} {t.get('weight', 0):>6.2f}  {sources}")

    # Universe
    universe = result.get("candidate_universe", [])
    print(f"\n  候选池: {result['universe_size']} 只股票")
    print(f"  {'代码':<12s} {'名称':<10s} {'相关性':>8s} {'主题来源'}")
    print(f"  {'-' * 60}")
    for s in universe[: args.limit if hasattr(args, "limit") else 20]:
        topics_str = ", ".join(s.get("source_topics", []))[:30]
        print(
            f"  {s.get('ts_code', '')!s:<12s} {s.get('name', '')!s:<10s} "
            f"{float(s.get('relevance', 0)):>8.3f}  {topics_str}"
        )

    print(f"\n  输出目录: {result.get('output_dir', 'N/A')}")
    if result.get("holdings_overlay_artifact"):
        print(f"  持仓资格: {result['holdings_overlay_artifact']}")


def cmd_universe(args: argparse.Namespace) -> None:
    """Show candidate universe output."""
    date_int = _resolve_date(args.date)

    # Find output dir
    if date_int:
        out_dir = OUTPUTS_DIR / date_int
    else:
        # Most recent
        candidates = sorted(OUTPUTS_DIR.iterdir()) if OUTPUTS_DIR.is_dir() else []
        out_dir = candidates[-1] if candidates else None

    if out_dir is None or not out_dir.is_dir():
        print("No universe output found. Run `hotspot run` first.")
        return

    json_path = out_dir / "candidate_universe.json"
    csv_path = out_dir / "candidate_universe.csv"

    if args.csv and csv_path.exists():
        import pandas as pd

        df = pd.read_csv(csv_path)
        print(df.head(args.limit).to_string(index=False))
        return

    if json_path.exists():
        with open(json_path) as f:
            data = json.load(f)
        universe = data.get("candidate_universe", [])
        date_str = data.get("date", out_dir.name)

        print(f"\n  候选池 {date_str} ({len(universe)} 只股票)")
        print(f"  输出目录: {out_dir}")
        print(f"  {'代码':<12s} {'名称':<10s} {'相关性':>8s} {'主题来源'}")
        print(f"  {'-' * 60}")
        for s in universe[: args.limit]:
            topics_str = ", ".join(s.get("source_topics", []))[:30]
            print(
                f"  {s.get('ts_code', '')!s:<12s} {s.get('name', '')!s:<10s} "
                f"{float(s.get('relevance', 0)):>8.3f}  {topics_str}"
            )

        # Also show topics
        topics = data.get("topics", [])
        if topics:
            print("\n  主题空间:")
            for t in topics:
                print(
                    f"    {t.get('topic', ''):<25s} w={t.get('weight', 0):.2f}  "
                    f"{t.get('reasoning', '')}"
                )


def cmd_build_prompt(args: argparse.Namespace) -> None:
    """Collect hotspot data and write LLM prompt to file."""
    config = _resolve_config(args.config)
    builder = Screener(config)
    result = builder.build_prompt(
        trade_date=args.date,
        stock_limit=args.stock_limit,
        concept_limit=args.concept_limit,
    )

    out_path = Path(args.out_prompt)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result["prompt"], encoding="utf-8")

    print(f"\n  Prompt 已写入: {out_path.resolve()}")
    print(f"  日期:         {result['date']}")
    print(f"  热股数:       {result['stock_count']}")
    print(f"  概念板块数:   {result['concept_count']}")
    print(f"  提示词长度:   {result['prompt_length']} 字符")
    print(f"  行业信号:     {'有' if result['industry_signal_available'] else '无'}")
    print()
    print("  下一步: 读取 prompt 文件，做主题分类，输出 topics.json，然后运行:")
    print(f"    hotsector run --date {result['date_int']} --load-topics topics.json")


def _resolve_output_dir(date_arg: str | None) -> Path | None:
    date_int = _resolve_date(date_arg)
    if date_int:
        return OUTPUTS_DIR / date_int
    candidates = sorted(OUTPUTS_DIR.iterdir()) if OUTPUTS_DIR.is_dir() else []
    return candidates[-1] if candidates else None


def cmd_export_signals(args: argparse.Namespace) -> None:
    """Export a saved candidate universe as canonical research signals."""
    if args.input:
        input_path = Path(args.input)
        default_out_dir = input_path.parent
    else:
        default_out_dir = _resolve_output_dir(args.date)
        if default_out_dir is None:
            print("No universe output found. Run `hotsector run` first.")
            return
        input_path = default_out_dir / "candidate_universe.json"

    if not input_path.exists():
        print(f"candidate_universe.json not found: {input_path}")
        return

    out_dir = Path(args.output_dir) if args.output_dir else default_out_dir
    try:
        result = load_candidate_result(input_path)
    except CandidateContractError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    files = write_signal_artifacts(
        result,
        out_dir,
        model_version=args.model_version,
        feature_set_id=args.feature_set_id,
    )
    print("\n  Signal artifacts:")
    for label, path in files.items():
        print(f"    {label}: {path}")


def cmd_validate_output(args: argparse.Namespace) -> None:
    """Validate a saved candidate universe and its canonical signal artifacts."""
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = _resolve_output_dir(args.date)
        if out_dir is None:
            print("No universe output found. Run `hotsector run` first.", file=sys.stderr)
            sys.exit(1)

    issues = validate_candidate_output(
        out_dir,
        required_sources=(
            parse_source_list(args.require_sources) if args.require_sources is not None else ()
        ),
        min_candidates=args.min_candidates,
        require_signals=not bool(args.no_require_signals),
    )
    if issues:
        print(f"  Output quality gate failed: {out_dir}", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        sys.exit(1)
    print(f"  Output quality gate passed: {out_dir}")


def cmd_validate_holdings_overlay(args: argparse.Namespace) -> None:
    """Validate the owner artifact and emit a consumer-safe canonical summary."""

    input_path = Path(args.input).expanduser()
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        overlay = validate_holdings_overlay(payload)
    except OSError as exc:
        print(f"ERROR: cannot read holdings overlay: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid holdings overlay JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    except HoldingsOverlayContractError as exc:
        print(f"ERROR: invalid holdings overlay: {exc}", file=sys.stderr)
        sys.exit(1)

    rows = overlay["rows"]
    policy = overlay["feature_policy"]
    summary = {
        "valid": True,
        "artifact_type": overlay["artifact_type"],
        "schema_version": overlay["schema_version"],
        "policy_id": policy["policy_id"],
        "policy_version": policy["version"],
        "policy_sha256": policy["canonical_sha256"],
        "observation_date": overlay["observation_date"],
        "candidate_artifact_type": overlay["candidate_artifact_type"],
        "candidate_schema_version": overlay["candidate_schema_version"],
        "candidate_payload_sha256": overlay["candidate_payload_sha256"],
        "row_count": len(rows),
        "incumbent_count": sum(row["is_current_holding"] is True for row in rows),
        "current_theme_match_count": sum(row["current_theme_match"] is True for row in rows),
        "entry_eligible_count": sum(row["entry_eligible"] is True for row in rows),
        "hold_eligible_count": sum(row["hold_eligible"] is True for row in rows),
        "canonical_sha256": canonical_sha256(overlay),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def _resolve_config(config_arg: str | None) -> dict:
    if config_arg:
        return load_config(config_arg)
    # Check for default config
    default_path = Path("configs/default.yml")
    if default_path.exists():
        return load_config(str(default_path))
    return default_config()


def _resolve_date(date_arg: str | None) -> str | None:
    if date_arg:
        return date_key(date_arg)
    return None


def cmd_backtest_stock(args: argparse.Namespace) -> None:
    """Run hotspot concept → stocks backtest."""
    import json

    result = run_stock_backtest(
        start_date=args.start,
        end_date=args.end,
        top_concepts=args.top_concepts,
        stocks_per_concept=args.stocks_per_concept,
        sample_every_n_days=args.sample,
        initial_capital=args.capital,
    )
    print("\n" + "=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_backtest_etf(args: argparse.Namespace) -> None:
    """Run hotspot concept → ETF rotation backtest."""
    import json

    result = run_etf_backtest(
        top_k=args.top_k,
        start_date=args.start,
        end_date=args.end,
        fee_rate=args.fee,
        initial_capital=args.capital,
    )
    print("\n" + "=" * 70)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_backtest_etf_ml(args: argparse.Namespace) -> None:
    """Run ML-enhanced hotspot → ETF rotation backtest."""
    import json

    result = run_etf_ml_backtest(
        start_date=args.start,
        end_date=args.end,
        top_k=args.top_k,
        fee_rate=args.fee,
        initial_capital=args.capital,
        model_type=args.model,
        walk_forward_step_days=args.step_days,
        min_train_days=args.min_train,
        effective_trials=args.trials,
    )
    print("\n" + "=" * 70)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "info": cmd_info,
        "latest-date": cmd_latest_date,
        "scan": cmd_scan,
        "run": cmd_run,
        "universe": cmd_universe,
        "build-prompt": cmd_build_prompt,
        "export-signals": cmd_export_signals,
        "validate-output": cmd_validate_output,
        "validate-holdings-overlay": cmd_validate_holdings_overlay,
    }

    # backtest has sub-subcommands
    if args.command == "backtest":
        bt_handlers = {
            "stock": cmd_backtest_stock,
            "etf": cmd_backtest_etf,
            "etf-ml": cmd_backtest_etf_ml,
        }
        handler = bt_handlers.get(args.bt_command)
        if handler:
            handler(args)
            return
        parser.print_help()
        sys.exit(1)

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
