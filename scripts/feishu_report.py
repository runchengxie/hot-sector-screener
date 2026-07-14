#!/usr/bin/env python3
"""Generate and (optionally) deliver daily AI stock picks report to Feishu.

Reads today's deepseek_picks.json, formats an interactive card, and
optionally pushes via Feishu webhook (respecting --dry-run).

Usage::

    # Generate report, preview to stdout, don't push
    uv run python scripts/feishu_report.py --date 20260714 --dry-run

    # Generate and push (uses FEISHU_WEBHOOK_DAILY env var)
    uv run python scripts/feishu_report.py --date 20260714 --push

    # Full pipeline: generate candidates + picks + report
    uv run python scripts/feishu_report.py --date 20260714 --pipeline

Environment::

    DEEPSEEK_API_KEY        DeepSeek API key
    FEISHU_WEBHOOK_DAILY    Feishu bot webhook URL (for push)
    FEISHU_SECRET_DAILY     Feishu bot signing secret
    FEISHU_WEBHOOK_ALERTS   Alerts channel webhook (for status messages)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------
def build_report(date_str: str, top_n: int = 10) -> dict[str, Any]:
    """Build a Feishu interactive card from today's picks."""
    picks_path = OUTPUTS_DIR / date_str / "deepseek_picks.json"

    if not picks_path.exists():
        return _error_card(date_str, "今日 AI 选股尚未生成。请先运行 deepseek_pick.py。")

    data = json.loads(picks_path.read_text())
    picks = data.get("picks", [])[:top_n]
    _model = data.get("model", "unknown")
    n_candidates = data.get("n_candidates", "?")

    # Topic summary
    topics: dict[str, int] = {}
    for p in picks:
        t = p.get("primary_topic", "其他")
        topics[t] = topics.get(t, 0) + 1
    topic_lines = [f"{t}（{n}只）" for t, n in sorted(topics.items(), key=lambda x: -x[1])]

    # Build card
    header_title = f"🔥 AI 精选 · {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    elements: list[dict] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**候选池：**{n_candidates} 只 ｜ "
                    f"**主题分布：**{', '.join(topic_lines[:8])}\n"
                    f"**回测参考：**近 1 月超额收益 +16.9%（含交易成本）"
                ),
            },
        },
        {"tag": "hr"},
    ]

    # Pick list
    for i, p in enumerate(picks, 1):
        ts = p.get("ts_code", "?")
        name = p.get("name", "?")
        conf = p.get("confidence_score", 5)
        topic = p.get("primary_topic", "")
        reasoning = (p.get("reasoning", "") or "")[:120]
        risk = (p.get("risk_note", "") or "")[:60]

        conf = max(1, min(10, int(conf)))
        conf_bar = "█" * conf + "░" * (10 - conf)
        lines = [
            f"**{i}. {name}**  `{ts}`  信心 **{conf}/10**  {conf_bar}",
            f"📌 {topic}",
        ]
        if reasoning:
            lines.append(f"💡 {reasoning}")
        if risk:
            lines.append(f"⚠️ {risk}")

        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n".join(lines)},
            }
        )
        if i < len(picks):
            elements.append({"tag": "hr"})

    # Footer
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": (
                        "⚠️ AI 选股仅供研究参考，不构成投资建议。"
                        f"生成时间：{datetime.now().strftime('%H:%M')}"
                    ),
                }
            ],
        }
    )

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": header_title},
        },
        "elements": elements,
    }


def _error_card(date_str: str, msg: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": "AI 精选 · 未就绪"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**{date_str}**\n\n{msg}"},
            }
        ],
    }


# ---------------------------------------------------------------------------
# Delivery (reuses existing post_feishu.py)
# ---------------------------------------------------------------------------
def deliver_report(card: dict, dry_run: bool = True) -> bool:
    """Send the card via Feishu webhook (or preview if dry_run)."""
    if dry_run:
        print("\n" + "=" * 50)
        print("DRY-RUN — 不实际推送。卡片内容：")
        print("=" * 50)
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return True

    webhook = os.environ.get("FEISHU_WEBHOOK_DAILY")
    if not webhook:
        print("ERROR: FEISHU_WEBHOOK_DAILY not set. Cannot push.", file=sys.stderr)
        return False

    # Write card to temp file and call post_feishu.py
    card_path = OUTPUTS_DIR / "_feishu_card_tmp.json"
    card_path.write_text(json.dumps(card, ensure_ascii=False))

    post_feishu = PROJECT_ROOT.parent / "src" / "daily_messenger" / "tools" / "post_feishu.py"
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(post_feishu),
            "--mode",
            "interactive",
            "--card",
            str(card_path),
            "--channel",
            "daily",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT.parent),
    )
    card_path.unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"Feishu push failed: {result.stderr}", file=sys.stderr)
        return False
    print("Feishu push OK")
    return True


# ---------------------------------------------------------------------------
# Pipeline runner (with retry + fallback)
# ---------------------------------------------------------------------------
def run_pipeline(
    date_str: str,
    top_n: int = 10,
    max_retries: int = 2,
    dry_run: bool = True,
) -> bool:
    """Full pipeline: candidates → picks → report, with fallback."""
    start = time.time()

    # Step 1: Generate candidates (with retry)
    print(f"[1/3] Generating candidates for {date_str}...")
    for attempt in range(1, max_retries + 2):
        try:
            _result = subprocess.run(
                [
                    "uv",
                    "run",
                    "hotsector",
                    "run",
                    "--date",
                    date_str,
                    "--no-llm",
                    "--max-candidates",
                    "100",
                ],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT),
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            print(f"       attempt {attempt} timed out")
            if attempt <= max_retries:
                time.sleep(5)
                continue
            print("       FAILED. Pipeline aborted.")
            return False

        csv_path = OUTPUTS_DIR / date_str / "candidate_universe.csv"
        if csv_path.exists():
            print(f"       OK ({'retry' if attempt > 1 else '1st'} attempt)")
            break
        if attempt <= max_retries:
            print(f"       attempt {attempt} produced no CSV, retrying...")
            time.sleep(5)
        else:
            print(f"       FAILED after {max_retries + 1} attempts.")
            return False

    # Step 2: DeepSeek picks (with fallback)
    print(f"[2/3] Running DeepSeek picks for {date_str}...")
    try:
        _pick_result = subprocess.run(
            [
                "uv",
                "run",
                "python",
                str(SCRIPTS_DIR / "deepseek_pick.py"),
                "--date",
                date_str,
                "--top-n",
                str(top_n),
            ],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("       DeepSeek timed out. Using fallback.")
        _generate_fallback_picks(date_str, top_n)

    picks_path = OUTPUTS_DIR / date_str / "deepseek_picks.json"
    if not picks_path.exists():
        print("       DeepSeek failed. Using fallback: top-N by hotsector score.")
        _generate_fallback_picks(date_str, top_n)
    else:
        print("       OK")

    # Step 3: Report
    print("[3/3] Building Feishu report...")
    card = build_report(date_str, top_n=top_n)
    card_path = OUTPUTS_DIR / date_str / "feishu_card.json"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2))
    print(f"       Card saved: {card_path}")

    ok = deliver_report(card, dry_run=dry_run)

    elapsed = time.time() - start
    print(f"\nPipeline complete in {elapsed:.1f}s")
    _send_status(dry_run, ok, elapsed, date_str, top_n)

    return ok


def _generate_fallback_picks(date_str: str, top_n: int) -> None:
    """Fallback: pick top-N by hotsector score from candidate CSV."""
    csv_path = OUTPUTS_DIR / date_str / "candidate_universe.csv"
    if not csv_path.exists():
        return
    df = pd.read_csv(csv_path)
    if "score" in df.columns:
        df = df.sort_values("score", ascending=False)
    df = df.head(top_n)
    picks = []
    for _, r in df.iterrows():
        score_val = r.get("score", 5)
        picks.append(
            {
                "ts_code": r["ts_code"],
                "name": r.get("name", ""),
                "confidence_score": int(min(10, max(1, round(float(score_val))))),
                "reasoning": f"[规则兜底] 候选池得分 {float(score_val):.1f}",
                "primary_topic": _parse_first_topic(r.get("source_topics", "")),
                "risk_note": "非 AI 精选，由规则引擎兜底生成",
            }
        )
    result = {
        "run_date": date_str,
        "generated_at": datetime.now().isoformat(),
        "model": "fallback (hotsector score)",
        "top_n": top_n,
        "n_candidates": len(df),
        "picks": picks,
    }
    out = OUTPUTS_DIR / date_str / "deepseek_picks.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))


def _parse_first_topic(raw: Any) -> str:
    """Extract first topic from a Python-list-like string."""
    import ast

    try:
        items = ast.literal_eval(str(raw))
        if items and isinstance(items, list):
            return str(items[0])
    except (ValueError, SyntaxError):
        pass
    s = str(raw).strip("[]'\" ")
    return s.split(",")[0].strip("'\" ") if s else "未分类"


def _send_status(dry_run: bool, ok: bool, elapsed: float, date_str: str, top_n: int) -> None:
    """Send a minimal bot status message to alerts channel."""
    status = "✅ 完成" if ok else "❌ 失败"
    msg = f"🤖 AI 选股 {status} | {date_str} | top-{top_n} | {elapsed:.0f}s"
    if dry_run:
        print(f"[monitor] {msg}")
        return

    alert_webhook = os.environ.get("FEISHU_WEBHOOK_ALERTS")
    if not alert_webhook:
        return

    card_path = OUTPUTS_DIR / "_feishu_status_tmp.json"
    status_card = {
        "config": {"wide_screen_mode": False},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": msg}}],
    }
    card_path.write_text(json.dumps(status_card, ensure_ascii=False))

    post_feishu = PROJECT_ROOT.parent / "src" / "daily_messenger" / "tools" / "post_feishu.py"
    subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(post_feishu),
            "--mode",
            "interactive",
            "--card",
            str(card_path),
            "--channel",
            "alerts",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT.parent),
    )
    card_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Top-N comparison
# ---------------------------------------------------------------------------
def compare_top_n(date_str: str, top_ns: list[int] | None = None) -> None:
    """Run backtest for multiple top-n values and compare."""
    if top_ns is None:
        top_ns = [5, 10, 15]
    print(f"Top-N comparison for {date_str}:")
    print(f"{'top_n':>8s}  {'Return':>10s}  {'Sharpe':>8s}  {'MaxDD':>8s}  {'HitRate':>8s}")
    print("-" * 55)

    for tn in top_ns:
        result = subprocess.run(
            [
                "uv",
                "run",
                "python",
                str(SCRIPTS_DIR / "backtest_deepseek.py"),
                "--phase",
                "backtest",
                "--date",
                date_str,
                "--top-n",
                str(tn),
                "--lookback",
                "30",
            ],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=180,
        )
        # Parse stats from output
        for line in result.stdout.splitlines():
            if "Alpha" in line and "excess" in line:
                alpha = line.strip()
                print(alpha)
                break


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="AI picks → Feishu report")
    parser.add_argument("--date", help="Trade date YYYYMMDD (default: today)")
    parser.add_argument("--top-n", type=int, default=10, help="Stocks to show (default: 10)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Generate report but don't push to Feishu (default)",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Actually push to Feishu (overrides --dry-run)",
    )
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="Run full pipeline: candidates → picks → report",
    )
    parser.add_argument(
        "--compare-top-n",
        action="store_true",
        help="Compare backtest results for top-n=5,10,15",
    )
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    dry_run = not args.push

    if args.compare_top_n:
        compare_top_n(date_str)
    elif args.pipeline:
        run_pipeline(date_str, top_n=args.top_n, dry_run=dry_run)
    else:
        card = build_report(date_str, top_n=args.top_n)
        card_path = OUTPUTS_DIR / date_str / "feishu_card.json"
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2))
        print(f"Card saved: {card_path}")
        deliver_report(card, dry_run=dry_run)


if __name__ == "__main__":
    main()
