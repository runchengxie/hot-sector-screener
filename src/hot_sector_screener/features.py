"""Feature engine adapted from guan-etf-rotation-v3 for A-share stock data.

Computes 25 technical indicators from OHLCV data. Designed to work with
single-stock DataFrames (pandas, date-indexed) or with the data lake's
daily parquet partitions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import ta
from scipy.stats import linregress

# All 25 feature columns produced by calculate_technical_features
ALL_FEATURE_COLUMNS = [
    "pvt",
    "kurtosis_60",
    "skew_60",
    "roc_12",
    "roc_18",
    "roc_20",
    "cumret_120",
    "ema_5",
    "macd",
    "macd_signal",
    "macd_diff",
    "atr_20",
    "atr_ratio_20",
    "ar",
    "br",
    "bear_power",
    "aroon_up",
    "aroon_down",
    "wvad",
    "vol_120",
    "dispersion_5",
    "trend_score",
    "vol_ratio",
    "high_low_ratio",
    "close_open_ratio",
]

# Reduced feature set (dropping noisy/intermediate columns) — matches rotation-v3 small_pool
SMALL_POOL_FEATURE_COLUMNS = [
    "kurtosis_60",
    "skew_60",
    "roc_12",
    "roc_18",
    "roc_20",
    "cumret_120",
    "macd_diff",
    "atr_ratio_20",
    "ar",
    "br",
    "aroon_up",
    "aroon_down",
    "vol_120",
    "dispersion_5",
    "vol_ratio",
    "high_low_ratio",
    "close_open_ratio",
]


def calculate_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute 25 technical indicators from an OHLCV DataFrame.

    Args:
        df: DataFrame with columns open, high, low, close, volume,
            date-indexed (or with a DatetimeIndex).

    Returns:
        DataFrame with the same index plus feature columns. NaN-filled
        where insufficient history exists.
    """
    frame = df.copy()

    # Ensure numeric
    for col in ("open", "high", "low", "close", "volume"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    # 1. PVT (Price Volume Trend)
    frame["pvt"] = (frame["close"].pct_change() * frame["volume"]).cumsum()

    # 2. N-day return kurtosis & skew
    frame["ret"] = frame["close"].pct_change()
    frame["kurtosis_60"] = frame["ret"].rolling(60).kurt()
    frame["skew_60"] = frame["ret"].rolling(60).skew()

    # 3. ROC (Rate of Change)
    frame["roc_12"] = frame["close"].pct_change(12) * 100
    frame["roc_18"] = frame["close"].pct_change(18) * 100
    frame["roc_20"] = frame["close"].pct_change(20) * 100

    # 4. 120-day cumulative return
    frame["cumret_120"] = (
        frame["close"].pct_change().add(1).rolling(120).apply(np.prod) - 1
    )

    # 5. EMA / MACD
    frame["ema_5"] = ta.trend.EMAIndicator(frame["close"], window=5).ema_indicator()
    macd_indicator = ta.trend.MACD(frame["close"])
    frame["macd"] = macd_indicator.macd()
    frame["macd_signal"] = macd_indicator.macd_signal()
    frame["macd_diff"] = macd_indicator.macd_diff()

    # 6. ATR
    if len(frame) >= 20:
        atr_indicator = ta.volatility.AverageTrueRange(
            frame["high"], frame["low"], frame["close"], window=20
        )
        frame["atr_20"] = atr_indicator.average_true_range()
        frame["atr_ratio_20"] = frame["atr_20"] / frame["close"]
    else:
        frame["atr_20"] = 0.0
        frame["atr_ratio_20"] = 0.0

    # 7. ARBR (popular Chinese technical indicator)
    if len(frame) >= 26:
        ar = (frame["high"] - frame["open"]).rolling(26).sum() / (
            frame["open"] - frame["low"]
        ).rolling(26).sum()
        br = (frame["high"] - frame["close"].shift(1)).rolling(26).sum() / (
            frame["close"].shift(1) - frame["low"]
        ).rolling(26).sum()
        frame["ar"] = ar
        frame["br"] = br
    else:
        frame["ar"] = 0.0
        frame["br"] = 0.0

    # 8. Bear Power (Elder Ray)
    if len(frame) >= 13:
        frame["ema_13"] = ta.trend.EMAIndicator(frame["close"], window=13).ema_indicator()
        frame["bear_power"] = frame["low"] - frame["ema_13"]
    else:
        frame["ema_13"] = frame["close"]
        frame["bear_power"] = 0.0

    # 9. Aroon
    if len(frame) >= 25:
        aroon = ta.trend.AroonIndicator(frame["high"], frame["low"], window=25)
        frame["aroon_up"] = aroon.aroon_up()
        frame["aroon_down"] = aroon.aroon_down()
    else:
        frame["aroon_up"] = 0.0
        frame["aroon_down"] = 0.0

    # 10. WVAD (Williams Variable Accumulation Distribution)
    hl_diff = (frame["high"] - frame["low"]).replace(0, np.nan)
    frame["wvad"] = (
        (frame["close"] - frame["open"]) / hl_diff * frame["volume"]
    ).fillna(0)

    # 11. Annualized volatility (120-day window)
    frame["vol_120"] = frame["ret"].rolling(120).std() * np.sqrt(252)

    # 12. 5-day dispersion
    frame["dispersion_5"] = (
        frame["close"].rolling(5).std() / frame["close"].rolling(5).mean()
    )

    # 13. Trend score (linear regression slope over 25 days)
    def _calc_trend(prices, period=25):
        if len(prices) < period:
            return np.nan
        window = prices.iloc[-period:]
        slope, _, _, _, _ = linregress(range(len(window)), window.values)
        return slope * period

    frame["trend_score"] = frame["close"].rolling(25).apply(
        lambda x: _calc_trend(x, 25), raw=False
    )

    # 14. Volume indicators
    frame["ma_vol_5"] = frame["volume"].rolling(5).mean()
    frame["ma_vol_20"] = frame["volume"].rolling(20).mean()
    frame["vol_ratio"] = frame["ma_vol_5"] / (frame["ma_vol_20"] + 1e-9)

    # 15. Price ratios
    frame["high_low_ratio"] = frame["high"] / frame["low"]
    frame["close_open_ratio"] = frame["close"] / frame["open"]

    # Fill NaN → 0
    frame = frame.ffill().fillna(0)

    return frame


def extract_feature_dict(features_df: pd.DataFrame, date=None) -> dict[str, float]:
    """Extract the latest feature row as a dict of {feature_name: value}.

    Args:
        features_df: Output from calculate_technical_features.
        date: Optional date to extract. If None, uses the last row.

    Returns:
        Dict of ALL_FEATURE_COLUMNS keys to float values.
    """
    if features_df is None or features_df.empty:
        return {col: 0.0 for col in ALL_FEATURE_COLUMNS}

    if date is not None and date in features_df.index:
        row = features_df.loc[date]
    else:
        row = features_df.iloc[-1]

    return {col: float(row.get(col, 0.0)) for col in ALL_FEATURE_COLUMNS}


def resolve_feature_columns(feature_set: str = "small_pool") -> list[str]:
    """Resolve which feature columns to use for model training.

    Args:
        feature_set: "all" or "small_pool" (default).

    Returns:
        List of feature column names.
    """
    if feature_set == "all":
        return list(ALL_FEATURE_COLUMNS)
    return list(SMALL_POOL_FEATURE_COLUMNS)
