"""Read rotation-v3 industry-signal outputs from DATA_PLATFORM_ROOT."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from ..observation_time import date_key


def _resolve_rotation_output_root() -> Path | None:
    root = os.environ.get("DATA_PLATFORM_ROOT")
    if not root:
        return None
    base = Path(root).expanduser().resolve() / "strategy_outputs" / "etf_rotation_v3"
    return base if base.is_dir() else None


def _optional_date_key(value: object) -> str | None:
    try:
        return date_key(value)
    except ValueError:
        return None


def _empty_signal(as_of_date: str, reason: str) -> pd.DataFrame:
    frame = pd.DataFrame()
    frame.attrs.update(
        {
            "as_of_date": as_of_date,
            "provenance_level": "unavailable",
            "strict_point_in_time": False,
            "publisher_receipt_verified": False,
            "reason": reason,
        }
    )
    return frame


def _read_as_of_signal(csv_path: Path, as_of_date: str) -> tuple[str, pd.DataFrame] | None:
    try:
        frame = pd.read_csv(csv_path)
    except (OSError, pd.errors.ParserError):
        return None
    if frame.empty or "signal_date" not in frame.columns:
        return None

    date_keys = frame["signal_date"].map(_optional_date_key)
    eligible_dates = [value for value in date_keys.dropna().unique() if value <= as_of_date]
    if not eligible_dates:
        return None

    signal_date = max(eligible_dates)
    selected = frame.loc[date_keys == signal_date].copy()
    selected["signal_date"] = signal_date
    selected.attrs.update(
        {
            "as_of_date": as_of_date,
            "signal_date": signal_date,
            "source_path": str(csv_path),
            "provenance_level": "signal_date_only",
            "strict_point_in_time": False,
            "publisher_receipt_verified": False,
        }
    )
    return signal_date, selected


def load_industry_signal(
    *,
    as_of_date: str,
    run_dir: str | None = None,
) -> pd.DataFrame:
    """Load the latest rotation-v3 industry signal available by ``as_of_date``.

    Returns columns: rank, industry, weight, weighted_score, symbol_count, signal_date
    and never falls forward to a signal produced after the requested date.
    """
    as_of = date_key(as_of_date)

    base = _resolve_rotation_output_root()
    if base is None:
        return _empty_signal(as_of, "rotation_output_root_unavailable")

    if run_dir:
        configured = Path(run_dir).expanduser()
        signal_dirs = [configured if configured.is_absolute() else base / configured]
    else:
        signal_dirs = sorted(path for path in base.iterdir() if path.is_dir())

    eligible: list[tuple[str, str, pd.DataFrame]] = []
    for signal_dir in signal_dirs:
        csv_path = signal_dir / "industry_signal.csv"
        if not csv_path.is_file():
            continue
        loaded = _read_as_of_signal(csv_path, as_of)
        if loaded is not None:
            signal_date, frame = loaded
            eligible.append((signal_date, str(csv_path), frame))

    if not eligible:
        return _empty_signal(as_of, "no_signal_on_or_before_as_of_date")
    return max(eligible, key=lambda item: (item[0], item[1]))[2]
