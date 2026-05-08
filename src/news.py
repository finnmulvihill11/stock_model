import os
import anthropic
from dotenv import load_dotenv
from src.fetcher import fetch_news, fetch_info

load_dotenv()
_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def analyze_company(ticker: str, extra_context: str = "") -> dict:
    news_items = fetch_news(ticker)
    info = fetch_info(ticker)

    company_name = info.get("longName", ticker)
    sector = info.get("sector", "Unknown")
    industry = info.get("industry", "Unknown")
    description = info.get("longBusinessSummary", "")[:500]

    headlines = "\n".join(
        f"- {item.get('title', '')}" for item in news_items[:15]
    ) or "No recent news available."

    prompt = f"""You are analyzing {company_name} ({ticker}) for a mid-to-long-term equity investor.

Company: {company_name}
Sector: {sector} | Industry: {industry}
Business: {description}

Recent news headlines:
{headlines}

{f"Additional context: {extra_context}" if extra_context else ""}

Provide a concise investment analysis covering:
1. Overall news sentiment (positive/neutral/negative) and why
2. Any significant recent events (earnings, management changes, product news, regulatory issues)
3. Macro/sector tailwinds or headwinds relevant to this company
4. Competitive position — any notable threats or strengths mentioned recently
5. Your holistic verdict for a mid/long-term investor considering a position

Format your response as JSON with these exact keys:
{{
  "sentiment": "positive" | "neutral" | "negative",
  "health": "healthy" | "neutral" | "deteriorating",
  "key_events": ["event1", "event2"],
  "macro_context": "one sentence on macro/sector environment",
  "competitive_notes": "one sentence on competitive position",
  "red_flags": ["flag1"] or [],
  "verdict": "one sentence final judgment",
  "confidence": "high" | "medium" | "low"
}}"""

    try:
        response = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        result["ticker"] = ticker
        return result
    except Exception as e:
        return {
            "ticker": ticker,
            "sentiment": "neutral",
            "health": "neutral",
            "key_events": [],
            "macro_context": "Analysis unavailable",
            "competitive_notes": "Analysis unavailable",
            "red_flags": [],
            "verdict": f"Claude API error: {str(e)}",
            "confidence": "low",
        }
