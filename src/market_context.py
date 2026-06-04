import os
import json
import requests
import pandas as pd
import yaml
import anthropic
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from src.fetcher import fetch_ohlcv, fetch_news

load_dotenv()
CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
MC = CONFIG["market_context"]

_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
_GEO_TTL = timedelta(hours=24)
_geo_client = None


def _get_geo_client() -> anthropic.Anthropic:
    global _geo_client
    if _geo_client is None:
        _geo_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _geo_client


def get_geopolitical_context() -> dict:
    """Assess current geopolitical risk from macro proxy headlines via Claude Haiku. Cached 24h."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_DIR / "geopolitical_context.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text())
            if datetime.now() - datetime.fromisoformat(data["cached_at"]) < _GEO_TTL:
                return data["result"]
        except Exception:
            pass

    try:
        spy_news = fetch_news("SPY")
        gld_news = fetch_news("GLD")
        headlines = "\n".join(
            f"- {item.get('title', '')}"
            for item in (spy_news[:8] + gld_news[:4])
            if item.get("title")
        ) or "No recent headlines available."
    except Exception:
        headlines = "Unable to fetch headlines."

    prompt = f"""Assess the current geopolitical risk environment for a US equity investor based on these market news headlines.

Headlines:
{headlines}

Focus only on geopolitical factors: trade wars, tariffs, sanctions, military conflicts, political instability, regulatory crackdowns. Ignore purely company-specific or routine market noise.

Respond in JSON only:
{{"risk_level":"low"|"moderate"|"high"|"severe","key_risks":["..."],"affected_sectors":["..."],"note":"one sentence summary of the geopolitical environment","confidence":"high"|"medium"|"low"}}"""

    try:
        response = _get_geo_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        cache_path.write_text(json.dumps({"cached_at": datetime.now().isoformat(), "result": result}))
        return result
    except Exception as e:
        return {"risk_level": "low", "key_risks": [], "affected_sectors": [], "note": f"Unavailable: {e}", "confidence": "low"}


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
    geo = get_geopolitical_context()
    return {"vix": vix, "fear_greed": fg, "geopolitical": geo}
