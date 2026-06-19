from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import default_config, load_config
from .data_sources.platform import list_available_dates, summarize_data_coverage
from .paths import OUTPUTS_DIR
from .universe_builder import HotspotUniverseBuilder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hotspot")
    sub = parser.add_subparsers(dest="command", required=True)

    # info — show data coverage
    info = sub.add_parser("info", help="Show available hotspot data in data lake")
    info.add_argument("--source", default=None, help="Filter by source name")

    # scan — collect data without LLM
    scan = sub.add_parser("scan", help="Collect hotspot data (no LLM call)")
    scan.add_argument("--date", default=None, help="Trade date (YYYY-MM-DD or YYYYMMDD)")
    scan.add_argument("--config", default=None, help="Config YAML path")

    # run — full pipeline with LLM
    run = sub.add_parser("run", help="Full pipeline: collect → LLM → map → universe")
    run.add_argument("--date", default=None, help="Trade date (YYYY-MM-DD or YYYYMMDD)")
    run.add_argument("--config", default=None, help="Config YAML path")
    run.add_argument("--no-llm", action="store_true", help="Skip LLM, use fallback topic extraction")
    run.add_argument("--output-dir", default=None, help="Custom output directory")
    run.add_argument("--max-candidates", type=int, default=None, help="Override max candidates")
    run.add_argument("--stocks-per-topic", type=int, default=None, help="Override stocks per topic")
    run.add_argument("--load-topics", default=None, help="Path to topics JSON file (skip LLM, use pre-classified topics)")

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

    return parser


def cmd_info(args: argparse.Namespace) -> None:
    """Show available hotspot data coverage."""
    coverage = summarize_data_coverage()
    print(f"{'='*60}")
    print(f"  热点数据湖覆盖情况")
    print(f"{'='*60}")
    for source, info in coverage.items():
        print(f"\n  {source}:")
        print(f"    可用交易日: {info['available_dates']}")
        print(f"    最早:       {info['earliest']}")
        print(f"    最晚:       {info['latest']}")
        if info["sample_dates"]:
            print(f"    样本日期:   {', '.join(str(d) for d in info['sample_dates'])}")


def cmd_scan(args: argparse.Namespace) -> None:
    """Collect hotspot data without LLM."""
    config = _resolve_config(args.config)
    builder = HotspotUniverseBuilder(config)
    result = builder.scan(trade_date=args.date)

    print(f"\n  扫描日期: {result['date']}")
    print(f"  {'='*40}")
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
        print(f"  {'-'*60}")
        for s in ths:
            print(f"  {str(s.get('rank', '')):>4s} {str(s.get('ts_code', '')):<12s} "
                  f"{str(s.get('ts_name', '')):<10s} {str(s.get('hot', '')):>6s} "
                  f"{s.get('concept', '')[:30]}")


def cmd_run(args: argparse.Namespace) -> None:
    """Full pipeline run."""
    config = _resolve_config(args.config)

    # CLI overrides
    if args.no_llm:
        config.setdefault("llm", {})["enabled"] = False
    if args.max_candidates is not None:
        config.setdefault("universe", {})["max_candidates"] = args.max_candidates
    if args.stocks_per_topic is not None:
        config.setdefault("universe", {})["stocks_per_topic"] = args.stocks_per_topic

    builder = HotspotUniverseBuilder(config)

    # Load pre-classified topics if --load-topics given
    pre_classified = None
    if args.load_topics:
        topics_path = Path(args.load_topics)
        if not topics_path.exists():
            print(f"ERROR: topics file not found: {topics_path}")
            sys.exit(1)
        with open(topics_path) as f:
            pre_classified = json.load(f)
        print(f"  加载预分类主题 ({len(pre_classified)} 个):")
        for t in pre_classified:
            print(f"    {t.get('topic', ''):<25s} w={t.get('weight', 0):.2f}")

    result = builder.build_universe(
        trade_date=args.date,
        output_dir=args.output_dir,
        topics=pre_classified,
    )

    print(f"\n  运行日期: {result['date']}")
    print(f"  生成时间: {result['generated_at']}")
    print(f"  {'='*40}")

    # Topics
    print(f"\n  今日主题空间 ({len(result['topics'])} 个主题):")
    print(f"  {'主题':<30s} {'权重':>6s} {'来源'}")
    print(f"  {'-'*60}")
    for t in result["topics"]:
        sources = ", ".join(t.get("source_signals", []))
        print(f"  {t.get('topic', ''):<30s} {t.get('weight', 0):>6.2f}  {sources}")

    # Universe
    universe = result.get("candidate_universe", [])
    print(f"\n  候选池: {result['universe_size']} 只股票")
    print(f"  {'代码':<12s} {'名称':<10s} {'相关性':>8s} {'主题来源'}")
    print(f"  {'-'*60}")
    for s in universe[:args.limit if hasattr(args, "limit") else 20]:
        topics_str = ", ".join(s.get("source_topics", []))[:30]
        print(f"  {str(s.get('ts_code', '')):<12s} {str(s.get('name', '')):<10s} "
              f"{float(s.get('relevance', 0)):>8.3f}  {topics_str}")

    print(f"\n  输出目录: {result.get('output_dir', 'N/A')}")


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
        print(f"  {'-'*60}")
        for s in universe[:args.limit]:
            topics_str = ", ".join(s.get("source_topics", []))[:30]
            print(f"  {str(s.get('ts_code', '')):<12s} {str(s.get('name', '')):<10s} "
                  f"{float(s.get('relevance', 0)):>8.3f}  {topics_str}")

        # Also show topics
        topics = data.get("topics", [])
        if topics:
            print(f"\n  主题空间:")
            for t in topics:
                print(f"    {t.get('topic', ''):<25s} w={t.get('weight', 0):.2f}  "
                      f"{t.get('reasoning', '')}")


def cmd_build_prompt(args: argparse.Namespace) -> None:
    """Collect hotspot data and write LLM prompt to file."""
    config = _resolve_config(args.config)
    builder = HotspotUniverseBuilder(config)
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
    print(f"  下一步: 读取 prompt 文件，做主题分类，输出 topics.json，然后运行:")
    print(f"    hotspot run --date {result['date_int']} --load-topics topics.json")


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
        return date_arg.replace("-", "")
    return None


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "info": cmd_info,
        "scan": cmd_scan,
        "run": cmd_run,
        "universe": cmd_universe,
        "build-prompt": cmd_build_prompt,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
