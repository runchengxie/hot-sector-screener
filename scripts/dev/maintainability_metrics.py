#!/usr/bin/env python3
"""Collect lightweight maintainability metrics for the repository.

Usage:
  python scripts/dev/maintainability_metrics.py           # human-readable text
  python scripts/dev/maintainability_metrics.py --json    # machine-readable JSON
  python scripts/dev/maintainability_metrics.py --ratchet # fail if budgets exceeded
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

# ── Per-project configuration ────────────────────────────────────────────────
# Override these after importing or edit in-place per project.

DEFAULT_ROOTS: tuple[str, ...] = ("src", "scripts", "tests")
DEFAULT_LIMIT = 10

# Ratchet budgets: freeze current state. After initial setup, these should match
# the output of `--json` and only be tightened (never loosened).
DEFAULT_RATCHET_BUDGETS: dict[str, int] = {
    "long_lines_over_100": 0,
    "functions_over_100": 6,
    "functions_over_250": 1,
    "functions_over_500": 0,
    "c901_file_ignores": 0,
    "files_over_800": 0,
    "files_over_1200": 0,
    "tests_over_1000": 0,
}


# ── Data types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FileMetric:
    path: str
    lines: int
    long_lines_over_100: int


@dataclass(frozen=True)
class FunctionMetric:
    path: str
    name: str
    start_line: int
    end_line: int
    lines: int


@dataclass(frozen=True)
class Metrics:
    roots: list[str]
    python_files: int
    python_lines: int
    long_lines_over_100: int
    functions_over_100: int
    functions_over_250: int
    functions_over_500: int
    c901_file_ignores: int
    files_over_800: int
    files_over_1200: int
    tests_over_1000: int
    largest_files: list[FileMetric]
    largest_functions: list[FunctionMetric]

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["thresholds"] = {
            "long_line_columns": 100,
            "large_function_lines": 100,
            "very_large_function_lines": 250,
            "huge_function_lines": 500,
            "large_file_lines": 800,
            "very_large_file_lines": 1200,
            "large_test_file_lines": 1000,
        }
        return payload


# ── Discovery ────────────────────────────────────────────────────────────────


def _find_repo_root() -> Path:
    """Walk up from this script until we find pyproject.toml."""
    candidate = Path(__file__).resolve().parent
    for _ in range(6):
        if (candidate / "pyproject.toml").is_file():
            return candidate
        candidate = candidate.parent
    # Fallback: assume script is at <repo>/scripts/dev/
    return Path(__file__).resolve().parents[2]


def _is_included_python_path(path: Path, roots: Sequence[str]) -> bool:
    return (
        path.suffix == ".py"
        and "__pycache__" not in path.parts
        and bool(path.parts)
        and path.parts[0] in roots
    )


def discover_python_files(
    repo_root: Path,
    roots: Sequence[str] = DEFAULT_ROOTS,
) -> list[Path]:
    files: list[Path] = []
    for root_name in roots:
        root = repo_root / root_name
        if root.exists():
            files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


# ── Metrics collection ───────────────────────────────────────────────────────


def _relative_path(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _function_metrics_for_file(repo_root: Path, path: Path, text: str) -> list[FunctionMetric]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    metrics: list[FunctionMetric] = []
    relative = _relative_path(repo_root, path)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end_line = getattr(node, "end_lineno", None)
        if end_line is None:
            continue
        metrics.append(
            FunctionMetric(
                path=relative,
                name=node.name,
                start_line=node.lineno,
                end_line=end_line,
                lines=end_line - node.lineno + 1,
            )
        )
    return metrics


def _c901_file_ignore_count(repo_root: Path) -> int:
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        return 0
    import tomllib

    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    per_file = config.get("tool", {}).get("ruff", {}).get("lint", {}).get("per-file-ignores", {})
    return sum(1 for values in per_file.values() if "C901" in values)


def collect_metrics(
    repo_root: Path,
    roots: Sequence[str] = DEFAULT_ROOTS,
    limit: int = DEFAULT_LIMIT,
) -> Metrics:
    files = discover_python_files(repo_root, roots)
    file_metrics: list[FileMetric] = []
    function_metrics: list[FunctionMetric] = []
    total_lines = 0
    total_long_lines = 0

    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        long_lines = sum(1 for line in lines if len(line) > 100)
        total_lines += len(lines)
        total_long_lines += long_lines
        file_metrics.append(
            FileMetric(
                path=_relative_path(repo_root, path),
                lines=len(lines),
                long_lines_over_100=long_lines,
            )
        )
        function_metrics.extend(_function_metrics_for_file(repo_root, path, text))

    largest_files = sorted(file_metrics, key=lambda item: item.lines, reverse=True)[:limit]
    largest_functions = sorted(function_metrics, key=lambda item: item.lines, reverse=True)[:limit]

    return Metrics(
        roots=list(roots),
        python_files=len(files),
        python_lines=total_lines,
        long_lines_over_100=total_long_lines,
        functions_over_100=sum(1 for item in function_metrics if item.lines > 100),
        functions_over_250=sum(1 for item in function_metrics if item.lines > 250),
        functions_over_500=sum(1 for item in function_metrics if item.lines > 500),
        c901_file_ignores=_c901_file_ignore_count(repo_root),
        files_over_800=sum(1 for item in file_metrics if item.lines > 800),
        files_over_1200=sum(1 for item in file_metrics if item.lines > 1200),
        tests_over_1000=sum(
            1 for item in file_metrics if item.lines > 1000 and item.path.startswith("tests/")
        ),
        largest_files=largest_files,
        largest_functions=largest_functions,
    )


def check_ratchet_budgets(
    metrics: Metrics,
    budgets: dict[str, int] | None = None,
) -> dict[str, dict[str, int]]:
    if budgets is None:
        budgets = DEFAULT_RATCHET_BUDGETS
    failures: dict[str, dict[str, int]] = {}
    for name, budget in budgets.items():
        value = getattr(metrics, name)
        if value > budget:
            failures[name] = {"actual": value, "budget": budget}
    return failures


# ── Output formatters ────────────────────────────────────────────────────────


def format_markdown(metrics: Metrics) -> str:
    lines = [
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Python files | {metrics.python_files} |",
        f"| Python lines | {metrics.python_lines} |",
        f"| Lines over 100 chars | {metrics.long_lines_over_100} |",
        f"| Functions over 100 lines | {metrics.functions_over_100} |",
        f"| Functions over 250 lines | {metrics.functions_over_250} |",
        f"| Functions over 500 lines | {metrics.functions_over_500} |",
        f"| C901 file ignores | {metrics.c901_file_ignores} |",
        f"| Files over 800 lines | {metrics.files_over_800} |",
        f"| Files over 1200 lines | {metrics.files_over_1200} |",
        f"| Test files over 1000 lines | {metrics.tests_over_1000} |",
        "",
        "Largest functions:",
        "",
        "| Lines | Function | Path |",
        "| ---: | --- | --- |",
    ]
    for item in metrics.largest_functions:
        lines.append(f"| {item.lines} | `{item.name}` | `{item.path}:{item.start_line}` |")
    return "\n".join(lines)


def format_text(metrics: Metrics) -> str:
    rows = [
        ("python_files", metrics.python_files),
        ("python_lines", metrics.python_lines),
        ("long_lines_over_100", metrics.long_lines_over_100),
        ("functions_over_100", metrics.functions_over_100),
        ("functions_over_250", metrics.functions_over_250),
        ("functions_over_500", metrics.functions_over_500),
        ("c901_file_ignores", metrics.c901_file_ignores),
        ("files_over_800", metrics.files_over_800),
        ("files_over_1200", metrics.files_over_1200),
        ("tests_over_1000", metrics.tests_over_1000),
    ]
    lines = ["Maintainability metrics:"]
    lines.extend(f"- {name}: {value}" for name, value in rows)
    lines.append("")
    lines.append("Largest functions:")
    lines.extend(
        f"- {item.lines} lines {item.path}:{item.start_line} {item.name}"
        for item in metrics.largest_functions
    )
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect static maintainability metrics.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output.")
    parser.add_argument("--markdown", action="store_true", help="Markdown table output.")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Largest N files/functions (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--ratchet",
        action="store_true",
        help="Fail if metrics exceed ratchet budgets.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = _find_repo_root()
    metrics = collect_metrics(repo_root, limit=max(args.limit, 0))

    if args.json:
        json.dump(metrics.to_payload(), sys.stdout, indent=2, ensure_ascii=False)
        print()
    elif args.markdown:
        print(format_markdown(metrics))
    else:
        print(format_text(metrics))

    if args.ratchet:
        failures = check_ratchet_budgets(metrics)
        if failures:
            print("\nMaintainability ratchet exceeded:", file=sys.stderr)
            for name, values in sorted(failures.items()):
                print(
                    f"- {name}: {values['actual']} > {values['budget']}",
                    file=sys.stderr,
                )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
