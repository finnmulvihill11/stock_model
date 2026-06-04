"""
Event-driven opportunity scanner.
Reads broad macro headlines, asks Claude Sonnet to identify major world events,
and surfaces specific stocks outside the standard screener universe that would
be meaningfully affected in the near term.
Runs weekly alongside the regular screener.
"""
import os
import json
import anthropic
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from src.fetcher import fetch_news, fetch_info
from src.signals import get_technical_signal

load_dotenv()
_client = None
EVENT_OPPS_FILE = Path(__file__).parent.parent / "data" / "event_opportunities.json"

_MACRO_PROXIES = ["SPY", "GLD", "TLT", "XLE", "XLF"]


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _fetch_macro_headlines() -> str:
    all_headlines = []
    for ticker in _MACRO_PROXIES:
        try:
            for item in fetch_news(ticker)[:5]:
                title = item.get("title", "")
                if title:
                    all_headlines.append(title)
        except Exception:
            continue
    seen, unique = set(), []
    for h in all_headlines:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return "\n".join(f"- {h}" for h in unique[:30]) or "No headlines available."


def _identify_events(headlines: str) -> list[dict]:
    prompt = f"""You are analyzing world events to find stock market opportunities for a swing trader with a weeks-to-months horizon.

Recent market headlines:
{headlines}

Identify 2-4 major world events or themes — from the headlines above AND any significant ongoing events you know about (wars, trade disputes, regulatory shifts, major IPOs, industry disruptions, geopolitical developments, sanctions, supply chain events).

For each event, name 3-5 specific US-listed stocks that would be most directly and meaningfully affected in the next few weeks to months. Prioritize second and third-order plays — the less-obvious beneficiaries or victims, NOT just the mega-cap names everyone already watches. Include recent IPOs, sector specialists, smaller mid-caps with direct exposure.

Only include events with a clear, specific near-term price catalyst. Skip vague macro themes.

Respond in JSON only:
{{
  "events": [
    {{
      "title": "short event name",
      "description": "what is happening and why it matters for markets",
      "direction": "bullish" | "bearish" | "mixed",
      "tickers": ["TICK1", "TICK2", "TICK3"],
      "rationale": "why these specific stocks and how they are affected",
      "timeframe": "expected catalyst window e.g. '2-6 weeks'",
      "confidence": "high" | "medium" | "low"
    }}
  ]
}}"""

    try:
        response = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip()).get("events", [])
    except Exception as e:
        print(f"  Event identification failed: {e}")
        return []


def _generate_event_plan(ticker: str, event: dict, signal: dict) -> dict:
    info = fetch_info(ticker)
    company_name = info.get("longName", ticker)

    prompt = f"""Brief swing trade assessment for {company_name} ({ticker}) as an event-driven opportunity.

Event: {event['title']}
{event['description']}
Direction: {event['direction']} | Timeframe: {event['timeframe']}
Why this stock: {event['rationale']}

Technical: {signal.get('tier', 'Hold')} | RSI: {signal.get('rsi', 'N/A')} | Price: ${signal.get('price', 0):.2f}

Respond in JSON only:
{{"entry_trigger": "specific condition to enter", "target_price": <number or null>, "exit_condition": "when to exit", "risk": "main risk to this thesis", "event_conviction": "high"|"medium"|"low"}}"""

    try:
        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception:
        return {"entry_trigger": "", "target_price": None, "exit_condition": "", "risk": "", "event_conviction": "low"}


def scan_world_events(existing_universe: set = None, max_tickers_per_event: int = 3) -> list[dict]:
    """
    Full event-driven scan. Returns grouped event results ready for caching.
    existing_universe: tickers already covered by the standard screener (for labeling only).
    """
    existing_universe = existing_universe or set()

    print("  Fetching macro headlines...")
    headlines = _fetch_macro_headlines()

    print("  Identifying world events via Claude Sonnet...")
    events = _identify_events(headlines)
    if not events:
        return []

    results = []
    for event in events:
        tickers = [t.upper().strip() for t in event.get("tickers", [])[:max_tickers_per_event]]
        print(f"  Event: {event['title']} → {tickers}")
        ticker_results = []
        for ticker in tickers:
            try:
                signal = get_technical_signal(ticker)
                plan = _generate_event_plan(ticker, event, signal)
                info = fetch_info(ticker)
                ticker_results.append({
                    "ticker": ticker,
                    "company_name": info.get("longName", ticker),
                    "sector": info.get("sector", ""),
                    "in_standard_universe": ticker in existing_universe,
                    "signal_tier": signal.get("tier", "Hold"),
                    "price": signal.get("price", 0),
                    "rsi": signal.get("rsi"),
                    "buy_score": signal.get("buy_score", 0),
                    "sell_score": signal.get("sell_score", 0),
                    "entry_trigger": plan.get("entry_trigger", ""),
                    "target_price": plan.get("target_price"),
                    "exit_condition": plan.get("exit_condition", ""),
                    "risk": plan.get("risk", ""),
                    "event_conviction": plan.get("event_conviction", "low"),
                })
            except Exception as e:
                print(f"    {ticker} failed: {e}")

        if ticker_results:
            results.append({
                "event_title": event["title"],
                "event_description": event["description"],
                "direction": event["direction"],
                "rationale": event["rationale"],
                "timeframe": event["timeframe"],
                "event_confidence": event["confidence"],
                "tickers": ticker_results,
            })

    return results


def save_event_opportunities(results: list[dict]) -> None:
    EVENT_OPPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVENT_OPPS_FILE, "w") as f:
        json.dump({"saved_at": datetime.now().isoformat(), "events": results}, f, indent=2)


def load_event_opportunities() -> dict:
    if not EVENT_OPPS_FILE.exists():
        return {"events": [], "saved_at": None, "stale": True, "age_hours": None}
    with open(EVENT_OPPS_FILE) as f:
        data = json.load(f)
    try:
        age_h = (datetime.now() - datetime.fromisoformat(data["saved_at"])).total_seconds() / 3600
        data["age_hours"] = round(age_h, 1)
        data["stale"] = age_h > 168
    except Exception:
        data["age_hours"] = None
        data["stale"] = True
    return data
