import os
import json
import yfinance as yf
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_HOURS = 8


def _cache_path(ticker: str, interval: str) -> Path:
    return CACHE_DIR / f"{ticker}_{interval}.parquet"


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=CACHE_TTL_HOURS)


def fetch_ohlcv(ticker: str, period: str = "2y", interval: str = "1d", force: bool = False) -> pd.DataFrame:
    path = _cache_path(ticker, interval)
    if not force and _is_cache_fresh(path):
        return pd.read_parquet(path)

    df = yf.download(ticker, period=period, interval=interval, auto_adjust=True, progress=False)
    if df.empty:
        raise ValueError(f"No data returned for {ticker}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df.to_parquet(path)
    return df


def fetch_ohlcv_weekly(ticker: str, period: str = "2y", force: bool = False) -> pd.DataFrame:
    return fetch_ohlcv(ticker, period=period, interval="1wk", force=force)


def fetch_multiple(tickers: list[str], period: str = "2y", interval: str = "1d") -> dict[str, pd.DataFrame]:
    return {t: fetch_ohlcv(t, period=period, interval=interval) for t in tickers}


def fetch_info(ticker: str) -> dict:
    path = CACHE_DIR / f"{ticker}_info.json"
    if _is_cache_fresh(path):
        with open(path) as f:
            return json.load(f)

    info = yf.Ticker(ticker).info
    with open(path, "w") as f:
        json.dump(info, f)
    return info


def fetch_news(ticker: str) -> list[dict]:
    t = yf.Ticker(ticker)
    return t.news or []
