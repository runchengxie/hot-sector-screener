#!/usr/bin/env python3
"""DeepSeek-powered stock picking from hot-sector candidate universe.

Reads a daily candidate_universe.csv produced by ``hotsector run`` and asks
DeepSeek to select the top 5-10 stocks with structured reasoning.

Usage::

    # Pick from the latest run (auto-detects latest outputs/<date>/)
    uv run python scripts/deepseek_pick.py

    # Pick from a specific date
    uv run python scripts/deepseek_pick.py --date 20260714

    # Choose model and pick count
    uv run python scripts/deepseek_pick.py --model deepseek-chat --top-n 5

Environment::

    DEEPSEEK_API_KEY   DeepSeek API key (required)
    LLM_API_URL        Override API base URL (default: https://api.deepseek.com/v1)
    LLM_MODEL          Override model (default: deepseek-chat)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------
def build_stock_pick_prompt(
    candidates: pd.DataFrame,
    run_date: str,
    top_n: int = 10,
    style: str = "momentum",
) -> str:
    """Build a prompt asking DeepSeek to pick stocks from the candidate pool.

    ``style``: "momentum" (short-term hot-money logic) or "buffett" (fundamental).
    """

    n_candidates = len(candidates)
    topic_summary = _summarise_topics(candidates)
    concept_summary = _summarise_concepts(candidates)

    persona = (
        "你是一个 A 股超短线交易员，专注热点题材的动量择时。"
        "你的核心逻辑是：跟踪游资和热钱流向，在题材发酵初期介入，"
        "吃一波短线动量后快速离场。你只看未来 1-3 天的短线空间。"
    )

    lines = [
        persona,
        "",
        f"## 分析日期: {run_date}",
        f"## 候选池: {n_candidates} 只股票",
        "",
        "### 今日主题分布",
        topic_summary,
        "",
        "### 今日概念分布",
        concept_summary,
        "",
        "---",
        "",
        "### 候选股票明细",
        "",
        "| ts_code | 名称 | 得分 | 关联主题 | 关联概念 "
        "| 日内确认 | 趋势 | 量能 | 风险 | 流动性 | 置信度 |",
        "|---------|------|------|----------|----------"
        "|----------|------|------|------|--------|--------|",
    ]

    for _, row in candidates.iterrows():
        topics = _fmt_list(row.get("source_topics", ""))
        concepts = _fmt_list(row.get("source_concepts", ""))
        lines.append(
            f"| {row.get('ts_code', '')} "
            f"| {row.get('name', '')} "
            f"| {_fmt_num(row.get('score'))} "
            f"| {topics} "
            f"| {concepts} "
            f"| {_fmt_num(row.get('daily_confirm_score'))} "
            f"| {_fmt_num(row.get('trend_score'))} "
            f"| {_fmt_num(row.get('volume_score'))} "
            f"| {_fmt_num(row.get('risk_score'))} "
            f"| {_fmt_num(row.get('liquidity_score'))} "
            f"| {row.get('confidence_label', '')} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 选股框架：短线动量逻辑",
            "",
            f"从以上候选池中选出恰好 {top_n} 只股票。按以下优先级排序：",
            "",
            "### 一级：量价共振（权重最高）",
            "- **量能分 ≈ 1.0** 且 **日内确认分 > 0.6**："
            "说明有真金白银在买，且盘中走势验证了题材逻辑。"
            "这种量价配合是短线最可靠的信号。",
            "- 量能分高但日内确认分低：可能是尾盘偷袭或对倒，谨慎。",
            "",
            "### 二级：主题强度与板块效应",
            "- 优先选主题分布中 **出现频次最高** 的主题下的股票（板块效应强，跟风盘多）",
            "- 同一主题下选 **日内确认 + 趋势** 双高的龙头",
            "- 板块内涨停家数多的主题优先（dc_concept 里的强度/涨停数）",
            "",
            "### 三级：风险过滤",
            "- **风险分 < 0.3** 的股票大概率是连板投机票或问题股，"
            "除非量能+趋势+日内确认三项都接近满分，否则回避",
            "- 流动性分 < 0.5（成交额分位 < 50%）的股票流动性太差，回避",
            "- 已经连续涨停 3 板以上的要注意开板风险",
            "",
            "### 四级：动量延续性",
            "- 趋势分 > 0.8 说明上升趋势确立，短线延续概率大",
            "- 综合得分 (score) 反映多维度加权，作为参考但不是唯一依据",
            "",
            "## 输出格式",
            "",
            "严格输出一个 JSON 数组，不要加任何其他文字、markdown 标记或解释。",
            "```json 和 ``` 都不要出现，直接以 [ 开头、以 ] 结尾。",
            "",
            "每个元素包含:",
            '  - "ts_code": 股票代码',
            '  - "name": 股票名称',
            '  - "confidence_score": 1-10 的整数，代表综合信心',
            '  - "reasoning": 选股逻辑简述（2-3 句），包含关键判断依据',
            '  - "primary_topic": 最相关的热点主题',
            '  - "risk_note": 一句话风险提示（选填，可为空字符串）',
            "",
            "示例:",
            '[{"ts_code": "000001.SZ", "name": "平安银行", '
            '"confidence_score": 8, "reasoning": "...", '
            '"primary_topic": "金融科技", "risk_note": ""}]',
        ]
    )

    return "\n".join(lines)


def _summarise_topics(df: pd.DataFrame) -> str:
    """Count topic frequency across candidates."""
    counter: dict[str, int] = {}
    for raw in df.get("source_topics", []):
        items = _parse_list(raw)
        for item in items:
            counter[item] = counter.get(item, 0) + 1
    if not counter:
        return "(无主题数据)"
    sorted_topics = sorted(counter.items(), key=lambda x: -x[1])[:15]
    return "\n".join(f"- {t} ({c} 只)" for t, c in sorted_topics)


def _summarise_concepts(df: pd.DataFrame) -> str:
    """Count concept frequency across candidates."""
    counter: dict[str, int] = {}
    for raw in df.get("source_concepts", []):
        items = _parse_list(raw)
        for item in items:
            counter[item] = counter.get(item, 0) + 1
    if not counter:
        return "(无概念数据)"
    sorted_concepts = sorted(counter.items(), key=lambda x: -x[1])[:15]
    return "\n".join(f"- {c} ({n} 只)" for c, n in sorted_concepts)


def _parse_list(raw: Any) -> list[str]:
    """Parse a value that might be a JSON list, Python literal list, or comma-sep string.

    Candidate CSVs often store lists as Python repr (single-quoted), not JSON,
    so we try ``ast.literal_eval`` before ``json.loads``.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        # Python literal (e.g. "['a', 'b']")
        if s.startswith("[") and s.endswith("]"):
            import ast

            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except (ValueError, SyntaxError):
                pass
            # JSON fallback (double-quoted)
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except (json.JSONDecodeError, TypeError):
                pass
        # Comma-separated plain string
        return [x.strip().strip("'\"") for x in s.split(",") if x.strip()]
    return []


def _fmt_list(raw: Any, max_items: int = 3) -> str:
    items = _parse_list(raw)[:max_items]
    return ",".join(items) if items else "—"


def _fmt_num(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    try:
        return f"{float(val):.2f}"
    except (ValueError, TypeError):
        return str(val)


# ---------------------------------------------------------------------------
# LLM caller (mirrors topic_classifier._call_llm_for_topics pattern)
# ---------------------------------------------------------------------------
def call_deepseek(prompt: str, model: str | None = None) -> str:
    """Call DeepSeek API and return the response text."""

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    api_url = os.environ.get("LLM_API_URL") or "https://api.deepseek.com/v1"
    model_name = model or os.environ.get("LLM_MODEL") or "deepseek-chat"

    if not api_key:
        raise RuntimeError("No DeepSeek API key configured. Set DEEPSEEK_API_KEY.")

    payload = json.dumps(
        {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是一个 A 股短线交易选股助手。只基于提供的候选池数据做出判断，"
                        "不做投资建议。严格输出 JSON 数组格式，不要加 markdown 标记。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 4096,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        raise RuntimeError(f"DeepSeek API call failed: {e}") from e


# ---------------------------------------------------------------------------
# Response parser (mirrors topic_classifier.parse_topic_response)
# ---------------------------------------------------------------------------
def parse_pick_response(text: str) -> list[dict[str, Any]]:
    """Parse DeepSeek response into structured pick list."""
    text = text.strip()

    # Strip markdown code fences
    if text.startswith("```json"):
        text = text.removeprefix("```json")
    elif text.startswith("```"):
        text = text.removeprefix("```")
    if text.endswith("```"):
        text = text.removesuffix("```")
    text = text.strip()

    try:
        picks = json.loads(text)
        if isinstance(picks, list):
            return picks
    except json.JSONDecodeError:
        pass

    # Fallback: find JSON array in text
    import re

    match = re.search(r"\[\s*\{.*?\}\s*\]", text, re.DOTALL)
    if match:
        try:
            picks = json.loads(match.group())
            if isinstance(picks, list):
                return picks
        except json.JSONDecodeError:
            pass

    return []


# ---------------------------------------------------------------------------
# Latest date discovery
# ---------------------------------------------------------------------------
def find_latest_date() -> str | None:
    """Return the most recent YYYYMMDD directory under outputs/."""
    if not OUTPUTS_DIR.exists():
        return None
    dates = sorted(
        [d.name for d in OUTPUTS_DIR.iterdir() if d.is_dir() and d.name.isdigit()],
        reverse=True,
    )
    return dates[0] if dates else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DeepSeek stock picking from hot-sector candidate universe"
    )
    parser.add_argument(
        "--date",
        help="Trade date (YYYYMMDD). Default: latest under outputs/",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of stocks to pick (default: 10)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="DeepSeek model name (default: deepseek-chat)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build prompt and print it, but do not call API",
    )
    return parser


def _print_pick_summary(picks: list[dict[str, Any]], top_n: int) -> None:
    print(f"\nTop {top_n} picks:")
    for i, p in enumerate(picks, 1):
        ts = p.get("ts_code", "?")
        name = p.get("name", "?")
        conf = p.get("confidence_score", "?")
        topic = p.get("primary_topic", "?")
        reasoning = (p.get("reasoning", "") or "")[:80]
        print(f"  {i:2d}. {ts} {name} [信心:{conf}] {topic}")
        if reasoning:
            print(f"      {reasoning}")


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    # Resolve date
    run_date = args.date or find_latest_date()
    if not run_date:
        print("ERROR: no --date given and no outputs/ directories found.", file=sys.stderr)
        sys.exit(1)

    run_dir = OUTPUTS_DIR / run_date
    csv_path = run_dir / "candidate_universe.csv"
    if not csv_path.exists():
        print(
            f"ERROR: {csv_path} not found. Run 'hotsector run --date {run_date}' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load candidates
    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} candidates from {csv_path}")

    # Sort by score descending so the prompt presents best candidates first
    if "score" in df.columns:
        df = df.sort_values("score", ascending=False)

    # Build prompt
    prompt = build_stock_pick_prompt(df, run_date, top_n=args.top_n)

    if args.dry_run:
        print("\n" + "=" * 60)
        print("PROMPT (dry-run)")
        print("=" * 60)
        print(prompt)
        return

    # Call DeepSeek
    print(f"Calling DeepSeek (model={args.model or 'deepseek-chat'}, top_n={args.top_n})...")
    try:
        response = call_deepseek(prompt, model=args.model)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Parse
    picks = parse_pick_response(response)
    if not picks:
        print("ERROR: could not parse valid picks from response.", file=sys.stderr)
        print("Raw response (first 500 chars):")
        print(response[:500])
        sys.exit(1)

    # Save result
    result = {
        "run_date": run_date,
        "generated_at": datetime.now().isoformat(),
        "model": args.model or os.environ.get("LLM_MODEL") or "deepseek-chat",
        "top_n": args.top_n,
        "n_candidates": len(df),
        "picks": picks,
    }

    out_path = run_dir / "deepseek_picks.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(picks)} picks to {out_path}")

    _print_pick_summary(picks, args.top_n)


if __name__ == "__main__":
    main()
