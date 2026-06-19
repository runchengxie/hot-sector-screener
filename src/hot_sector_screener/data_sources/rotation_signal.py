"""Read rotation-v3 industry-signal outputs from DATA_PLATFORM_ROOT."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


def _resolve_rotation_output_root() -> Path | None:
    root = os.environ.get("DATA_PLATFORM_ROOT")
    if not root:
        return None
    base = Path(root).expanduser().resolve() / "strategy_outputs" / "etf_rotation_v3"
    return base if base.is_dir() else None


def list_available_runs() -> list[str]:
    """List available rotation-v3 output run directories."""
    base = _resolve_rotation_output_root()
    if base is None:
        return []
    return sorted(str(d.name) for d in base.iterdir() if d.is_dir())


def load_industry_signal(run_dir: str | None = None) -> pd.DataFrame:
    """Load the industry_signal.csv from a rotation-v3 output run.

    Returns columns: rank, industry, weight, weighted_score, symbol_count, signal_date
    """
    base = _resolve_rotation_output_root()
    if base is None:
        return pd.DataFrame()

    if run_dir:
        signal_dir = base / run_dir
    else:
        candidates = sorted(base.iterdir()) if base.is_dir() else []
        signal_dir = candidates[-1] if candidates else None

    if signal_dir is None or not signal_dir.is_dir():
        return pd.DataFrame()

    csv_path = signal_dir / "industry_signal.csv"
    if not csv_path.exists():
        return pd.DataFrame()
    return pd.read_csv(csv_path)


def load_run_config(run_dir: str) -> dict | None:
    """Load run_config.json from a rotation-v3 output run."""
    base = _resolve_rotation_output_root()
    if base is None:
        return None
    config_path = base / run_dir / "run_config.json"
    if not config_path.exists():
        return None
    import json
    with open(config_path) as f:
        return json.load(f)
