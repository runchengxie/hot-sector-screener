"""Data quality checks adapted from guan-etf-rotation-v3.

Detects suspicious price jumps and other data anomalies that can
silently corrupt backtest results.
"""

from __future__ import annotations

import pandas as pd


def detect_suspicious_price_jumps(
    data_dict: dict[str, pd.DataFrame],
    threshold: float = 0.5,
) -> list[dict]:
    """Detect close-to-close returns exceeding a threshold (in absolute value).

    A single-day close-to-close return > 50% (default) is almost always a
    data error — a stock split not properly adjusted, a data feed glitch,
    or a stale price.

    Args:
        data_dict: Mapping of symbol → OHLCV DataFrame (must have 'close' column).
        threshold: Absolute return threshold (0.5 = 50%).

    Returns:
        List of issue dicts, sorted by worst jump magnitude descending.
        Each dict has: symbol, suspicious_days, worst_jump_date,
        worst_close_to_close_return_pct, previous_close, current_close.
    """
    issues = []
    resolved = abs(float(threshold))

    for symbol, frame in (data_dict or {}).items():
        if frame is None or frame.empty or "close" not in frame.columns:
            continue

        close_series = pd.Series(frame["close"], copy=False).astype(float)
        c2c = close_series.pct_change()
        suspicious = c2c[c2c.abs() > resolved]
        if suspicious.empty:
            continue

        worst_date = suspicious.abs().idxmax()
        prev_close = float(close_series.shift(1).loc[worst_date])
        curr_close = float(close_series.loc[worst_date])

        issues.append(
            {
                "symbol": symbol,
                "suspicious_days": len(suspicious),
                "worst_jump_date": pd.Timestamp(worst_date),
                "worst_close_to_close_return_pct": float(suspicious.loc[worst_date] * 100.0),
                "previous_close": prev_close,
                "current_close": curr_close,
            }
        )

    return sorted(
        issues,
        key=lambda item: abs(item["worst_close_to_close_return_pct"]),
        reverse=True,
    )
