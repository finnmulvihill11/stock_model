import pandas as pd
import numpy as np


def add_bollinger_bands(df: pd.DataFrame, window: int = 20, std: float = 2.0) -> pd.DataFrame:
    mid = df["Close"].rolling(window).mean()
    sigma = df["Close"].rolling(window).std()
    df["bb_mid"] = mid
    df["bb_upper"] = mid + std * sigma
    df["bb_lower"] = mid - std * sigma
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    df["bb_pct"] = (df["Close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
    return df


def add_rsi(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = df["Close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df


def add_moving_averages(df: pd.DataFrame, short: int = 50, long: int = 200) -> pd.DataFrame:
    df[f"ma{short}"] = df["Close"].rolling(short).mean()
    df[f"ma{long}"] = df["Close"].rolling(long).mean()
    return df


def add_volume_ma(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    df["volume_ma"] = df["Volume"].rolling(window).mean()
    df["volume_ratio"] = df["Volume"] / df["volume_ma"]
    return df


def add_atr(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = true_range.rolling(window).mean()
    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = add_bollinger_bands(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_moving_averages(df)
    df = add_volume_ma(df)
    df = add_atr(df)
    return df
