import pandas as pd
import numpy as np


def _find_local_lows(series: pd.Series, order: int = 5) -> pd.Series:
    lows = pd.Series(False, index=series.index)
    for i in range(order, len(series) - order):
        window = series.iloc[i - order:i + order + 1]
        if series.iloc[i] == window.min():
            lows.iloc[i] = True
    return lows


def _find_local_highs(series: pd.Series, order: int = 5) -> pd.Series:
    highs = pd.Series(False, index=series.index)
    for i in range(order, len(series) - order):
        window = series.iloc[i - order:i + order + 1]
        if series.iloc[i] == window.max():
            highs.iloc[i] = True
    return highs


def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 60, order: int = 5) -> dict:
    """
    Returns bullish and bearish RSI divergence detected in the most recent window.

    Bullish: price makes lower low, RSI makes higher low → upward reversal signal.
    Bearish: price makes higher high, RSI makes lower high → downward reversal signal.
    """
    if "rsi" not in df.columns:
        raise ValueError("RSI must be calculated before divergence detection")

    recent = df.tail(lookback).copy()
    price = recent["Close"]
    rsi = recent["rsi"]

    price_lows = _find_local_lows(price, order)
    rsi_lows = _find_local_lows(rsi, order)
    price_highs = _find_local_highs(price, order)
    rsi_highs = _find_local_highs(rsi, order)

    bullish = False
    bearish = False
    bull_reason = ""
    bear_reason = ""

    low_idx = price[price_lows].index
    if len(low_idx) >= 2:
        p1, p2 = low_idx[-2], low_idx[-1]
        if price[p2] < price[p1] and rsi[p2] > rsi[p1]:
            bullish = True
            bull_reason = (
                f"Price lower low ({price[p2]:.2f} < {price[p1]:.2f}) "
                f"but RSI higher low ({rsi[p2]:.1f} > {rsi[p1]:.1f})"
            )

    high_idx = price[price_highs].index
    if len(high_idx) >= 2:
        p1, p2 = high_idx[-2], high_idx[-1]
        if price[p2] > price[p1] and rsi[p2] < rsi[p1]:
            bearish = True
            bear_reason = (
                f"Price higher high ({price[p2]:.2f} > {price[p1]:.2f}) "
                f"but RSI lower high ({rsi[p2]:.1f} < {rsi[p1]:.1f})"
            )

    return {
        "bullish": bullish,
        "bearish": bearish,
        "bull_reason": bull_reason,
        "bear_reason": bear_reason,
    }
