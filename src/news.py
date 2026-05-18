import os
import json
import anthropic
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from src.fetcher import fetch_news, fetch_info

load_dotenv()
_client = None
CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
_NEWS_TTL = timedelta(hours=48)


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _load_cache(ticker: str) -> dict | None:
    path = CACHE_DIR / f"{ticker}_news.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if datetime.now() - datetime.fromisoformat(data["cached_at"]) < _NEWS_TTL:
            return data["result"]
    except Exception:
        pass
    return None


def _save_cache(ticker: str, result: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{ticker}_news.json").write_text(
        json.dumps({"cached_at": datetime.now().isoformat(), "result": result})
    )


def analyze_company(ticker: str, extra_context: str = "") -> dict:
    if not extra_context:
        cached = _load_cache(ticker)
        if cached:
            return cached

    news_items = fetch_news(ticker)
    info = fetch_info(ticker)
    company_name = info.get("longName", ticker)
    sector = info.get("sector", "Unknown")
    description = info.get("longBusinessSummary", "")[:300]
    headlines = "\n".join(f"- {item.get('title','')}" for item in news_items[:10]) or "No recent news."

    prompt = f"""Analyze {company_name} ({ticker}) — {sector} — for a mid-to-long-term investor.
Business: {description}

News:
{headlines}
{f"Context: {extra_context}" if extra_context else ""}

Respond in JSON only:
{{"sentiment":"positive"|"neutral"|"negative","health":"healthy"|"neutral"|"deteriorating","key_events":["..."],"macro_context":"one sentence","competitive_notes":"one sentence","red_flags":[],"verdict":"one sentence","confidence":"high"|"medium"|"low"}}"""

    try:
        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        result["ticker"] = ticker
        if not extra_context:
            _save_cache(ticker, result)
        return result
    except Exception as e:
        return {
            "ticker": ticker, "sentiment": "neutral", "health": "neutral",
            "key_events": [], "macro_context": "Unavailable",
            "competitive_notes": "Unavailable", "red_flags": [],
            "verdict": f"Error: {e}", "confidence": "low",
        }
