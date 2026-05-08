import requests
import pandas as pd
import yaml
from pathlib import Path
from src.fetcher import fetch_ohlcv

CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
MC = CONFIG["market_context"]


def get_vix() -> dict:
    df = fetch_ohlcv("^VIX", period="1mo", interval="1d")
    level = float(df["Close"].iloc[-1])
    if level >= MC["vix_fear_threshold"]:
        sentiment = "fear"
        note = f"VIX {level:.1f} — elevated fear, buy signals carry more weight"
    elif level <= MC["vix_greed_threshold"]:
        sentiment = "greed"
        note = f"VIX {level:.1f} — low volatility/complacency, raise sell sensitivity"
    else:
        sentiment = "neutral"
        note = f"VIX {level:.1f} — neutral market volatility"
    return {"level": level, "sentiment": sentiment, "note": note}


def get_fear_greed() -> dict:
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        data = resp.json()["data"][0]
        value = int(data["value"])
        label = data["value_classification"]
        return {"value": value, "label": label}
    except Exception:
        return {"value": None, "label": "unavailable"}


def get_relative_strength(ticker: str) -> dict:
    lookback = MC["rs_lookback_days"]
    benchmark = MC["benchmark"]

    try:
        stock_df = fetch_ohlcv(ticker, period="6mo")
        bench_df = fetch_ohlcv(benchmark, period="6mo")

        stock_ret = stock_df["Close"].pct_change(lookback).iloc[-1]
        bench_ret = bench_df["Close"].pct_change(lookback).iloc[-1]

        rs = stock_ret - bench_ret
        if rs > 0.05:
            label = "outperforming"
        elif rs < -0.05:
            label = "underperforming"
        else:
            label = "in-line"

        return {
            "ticker": ticker,
            "stock_return_3m": round(float(stock_ret) * 100, 2),
            "benchmark_return_3m": round(float(bench_ret) * 100, 2),
            "relative_strength": round(float(rs) * 100, 2),
            "label": label,
        }
    except Exception as e:
        return {"ticker": ticker, "label": "unavailable", "error": str(e)}


def get_market_context() -> dict:
    vix = get_vix()
    fg = get_fear_greed()
    return {"vix": vix, "fear_greed": fg}
