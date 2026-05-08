import os
import json
import anthropic
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from src.fetcher import fetch_info

load_dotenv()
_client = None

PLANS_DIR = Path(__file__).parent.parent / "data" / "plans"
PLANS_DIR.mkdir(parents=True, exist_ok=True)
MAX_HISTORY = 7  # keep last 7 daily snapshots per ticker


def _save_plan(ticker: str, plan: dict) -> None:
    path = PLANS_DIR / f"{ticker.upper()}.json"
    existing = _load_plan(ticker)
    history = existing.get("history", [])

    # Archive the previous plan into history before overwriting
    if existing.get("current"):
        snapshot = existing["current"].copy()
        snapshot["archived_at"] = datetime.now().isoformat()
        history.insert(0, snapshot)
        history = history[:MAX_HISTORY]

    with open(path, "w") as f:
        json.dump({"current": plan, "history": history}, f, indent=2)


def _load_plan(ticker: str) -> dict:
    path = PLANS_DIR / f"{ticker.upper()}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def get_current_plan(ticker: str) -> dict | None:
    return _load_plan(ticker).get("current")


def get_plan_history(ticker: str) -> list[dict]:
    return _load_plan(ticker).get("history", [])


def _load_portfolio_strategy() -> dict:
    path = PLANS_DIR / "PORTFOLIO_STRATEGY.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _save_portfolio_strategy(strategy: dict) -> None:
    path = PLANS_DIR / "PORTFOLIO_STRATEGY.json"
    existing = _load_portfolio_strategy()
    history = existing.get("history", [])
    if existing.get("current"):
        snapshot = existing["current"].copy()
        snapshot["archived_at"] = datetime.now().isoformat()
        history.insert(0, snapshot)
        history = history[:MAX_HISTORY]
    with open(path, "w") as f:
        json.dump({"current": strategy, "history": history}, f, indent=2)


def get_portfolio_strategy() -> dict | None:
    return _load_portfolio_strategy().get("current")


def get_portfolio_strategy_history() -> list[dict]:
    return _load_portfolio_strategy().get("history", [])


def generate_portfolio_strategy(
    portfolio: dict,
    position_plans: list[dict],
    market_context: dict,
    opportunity_plans: list[dict] = None,
    watchlist: list[dict] = None,
) -> dict:
    """Generate a high-level portfolio strategy with a ranked priority action list."""
    holdings_summary = "\n".join(
        f"- {p['ticker']}: {p.get('action','?')} | P&L {p.get('pnl_pct',0):+.1f}% | {p.get('timeframe','?')} | {p.get('action_reason','')[:80]}"
        for p in position_plans
    )
    opps_summary = ""
    if opportunity_plans:
        opps_summary = "\nNEW OPPORTUNITIES FROM SCREENER:\n" + "\n".join(
            f"- {p['ticker']}: {p.get('conviction','?').upper()} conviction | {p.get('buy_case','')[:80]}"
            for p in opportunity_plans[:5]
        )

    watchlist_summary = ""
    if watchlist:
        urgent = [w for w in watchlist if w.get("latest_signal", {}).get("final_tier") in ("Strong Buy", "Buy")]
        if urgent:
            watchlist_summary = "\nWATCHLIST — SIGNALS ACTIVE (HIGH PRIORITY):\n" + "\n".join(
                f"- {w['ticker']}: {w.get('latest_signal',{}).get('final_tier','?')} — added {w.get('added_date','')} | {w.get('reason','')[:60]}"
                for w in urgent
            )

    vix = market_context.get("vix", {})
    fg = market_context.get("fear_greed", {})
    flags = portfolio.get("flags", [])

    prompt = f"""You are a swing trading advisor writing a strategy brief for a busy MIT student investor.

PORTFOLIO SNAPSHOT:
- Total value: ${portfolio['total_value']:,.2f}
- Total P&L: ${portfolio['total_pnl']:+,.2f} ({portfolio['total_pnl_pct']:+.2f}%)
- Tech concentration: {portfolio['tech_pct']:.1f}%
- Flags: {', '.join(flags) or 'None'}

MARKET CONTEXT:
- {vix.get('note', '')}
- Fear & Greed: {fg.get('label', 'N/A')} ({fg.get('value', 'N/A')})

CURRENT POSITION PLANS:
{holdings_summary}
{opps_summary}
{watchlist_summary}

INVESTOR PROFILE:
- Swing to mid-term trader (weeks to a few months)
- Mean-reversion, buy oversold quality names, sell overbought
- $1,000 swing budget available
- Busy MIT student — needs a clear ranked action list above all else

Write a concise portfolio strategy. The priority_actions list is the MOST IMPORTANT part — it should be a ranked to-do list the investor can act on immediately. Include both current holdings AND new opportunities. Be specific: name the ticker, the action, and one concrete reason.

Respond in JSON:
{{
  "headline": "one sentence capturing current portfolio posture",
  "market_stance": "offensive" | "defensive" | "neutral",
  "overall_thesis": "2-3 sentences on the 4-8 week plan",
  "priority_actions": [
    {{
      "rank": 1,
      "action": "SELL" | "EXIT" | "ADD" | "BUY" | "WATCH" | "HOLD" | "TRIM",
      "ticker": "TICKER",
      "instruction": "specific one-line instruction e.g. 'Sell 2 shares — RSI 78, upper BB, thesis complete'",
      "urgency": "immediate" | "this week" | "this month" | "monitor"
    }}
  ],
  "biggest_risk": "single biggest risk right now",
  "what_to_watch": ["thing 1", "thing 2", "thing 3"],
  "on_course_check": ["condition plan is working", "condition plan needs revision"],
  "generated_at": "{datetime.now().isoformat()}"
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
        result = json.loads(text.strip())
        result["generated_at"] = datetime.now().isoformat()
        _save_portfolio_strategy(result)
        return result
    except Exception as e:
        fallback = {
            "headline": "Strategy generation failed",
            "market_stance": "neutral",
            "overall_thesis": str(e),
            "priority_actions": [],
            "biggest_risk": "",
            "what_to_watch": [],
            "on_course_check": [],
            "generated_at": datetime.now().isoformat(),
        }
        return fallback


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def generate_position_plan(
    holding: dict,
    signal: dict,
    fundamentals: dict,
    news: dict,
    earnings: dict,
    market_context: dict,
    final_tier: str,
) -> dict:
    """Generate a Claude-powered forward-looking plan for an existing position."""
    ticker = holding["ticker"]
    info = fetch_info(ticker)
    company_name = info.get("longName", ticker)

    shares = holding["shares"]
    avg_cost = holding["avg_cost"]
    current_price = holding["current_price"]
    pnl_pct = holding["unrealized_pnl_pct"]
    portfolio_pct = holding.get("portfolio_pct", 0)

    vix_note = market_context.get("vix", {}).get("note", "")
    fg = market_context.get("fear_greed", {})
    news_verdict = news.get("verdict", "")
    news_sentiment = news.get("sentiment", "neutral")
    fund_health = fundamentals.get("health", "neutral")
    fund_flags = fundamentals.get("flags", [])
    tech_reasons = signal.get("reasons", [])
    tech_misses = signal.get("misses", [])
    rsi = signal.get("rsi")
    earnings_status = earnings.get("verdict", {}).get("earnings_status", "unknown")
    days_to_earnings = earnings.get("verdict", {}).get("days_to_earnings")

    prompt = f"""You are a swing trading advisor analyzing a position for a busy MIT student investor.

INVESTOR PROFILE:
- Style: Swing to mid-term trader (hold horizon: weeks to a few months)
- Philosophy: Mean-reversion entries using Bollinger Bands, RSI (buy 20-30, sell 70-80), MACD, 200MA filter
- Risk: Moderate — quality companies only, no penny stocks
- Goal: Automated, explainable recommendations — user doesn't have time to monitor charts

POSITION: {company_name} ({ticker})
- Shares: {shares} @ avg cost ${avg_cost:.2f}
- Current price: ${current_price:.2f}
- Unrealized P&L: {pnl_pct:+.2f}%
- Portfolio weight: {portfolio_pct:.1f}%
- Signal tier: {final_tier}
- RSI: {rsi}

TECHNICAL SIGNALS FIRING:
{chr(10).join(f"✓ {r}" for r in tech_reasons) or "None"}

TECHNICAL SIGNALS MISSING:
{chr(10).join(f"✗ {m}" for m in tech_misses[:5]) or "None"}

FUNDAMENTALS: {fund_health.upper()}
{chr(10).join(f"• {f}" for f in fund_flags) or "• No flags"}

NEWS SENTIMENT: {news_sentiment.upper()}
{news_verdict}

EARNINGS: {earnings_status} {f"({days_to_earnings} days away)" if days_to_earnings else ""}

MARKET CONTEXT: {vix_note} | Fear & Greed: {fg.get('label', 'N/A')} ({fg.get('value', 'N/A')})

Generate a concise, opinionated forward-looking plan for this position. Be direct — tell the investor exactly what to do and why. Reflect their swing trading philosophy.

Respond in JSON with these exact keys:
{{
  "action": "Hold" | "Add More" | "Start Trimming" | "Exit" | "Wait",
  "action_reason": "1-2 sentence direct explanation of the recommended action",
  "target_price": <number or null>,
  "target_reasoning": "why this target makes sense given BB/RSI levels",
  "exit_trigger": "specific condition that should trigger exit (e.g. RSI hits 75, price touches upper BB)",
  "add_trigger": "specific condition to add more (e.g. RSI drops to 25 on volume confirmation)",
  "risk": "main risk to this plan",
  "outlook": "short" | "neutral" | "cautious",
  "timeframe": "estimated hold timeframe given current setup (e.g. 2-6 weeks)"
}}"""

    try:
        response = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        result["ticker"] = ticker
        result["company_name"] = company_name
        result["generated_at"] = datetime.now().isoformat()
        result["price_at_generation"] = current_price
        result["pnl_at_generation"] = pnl_pct
        _save_plan(ticker, result)
        return result
    except Exception as e:
        fallback = {
            "ticker": ticker,
            "company_name": company_name,
            "action": "Hold",
            "action_reason": f"Analysis unavailable: {str(e)}",
            "target_price": None,
            "target_reasoning": "",
            "exit_trigger": "",
            "add_trigger": "",
            "risk": "",
            "outlook": "neutral",
            "timeframe": "Unknown",
            "generated_at": datetime.now().isoformat(),
            "price_at_generation": current_price,
            "pnl_at_generation": pnl_pct,
        }
        _save_plan(ticker, fallback)
        return fallback


def generate_opportunity_plan(
    ticker: str,
    signal: dict,
    fundamentals: dict,
    news: dict,
    market_context: dict,
    final_tier: str,
    portfolio_value: float,
    sizing: dict,
) -> dict:
    """Generate a plan for a screener-surfaced stock not currently in the portfolio."""
    info = fetch_info(ticker)
    company_name = info.get("longName", ticker)
    sector = info.get("sector", "Unknown")

    vix_note = market_context.get("vix", {}).get("note", "")
    news_verdict = news.get("verdict", "")
    fund_health = fundamentals.get("health", "neutral")
    tech_reasons = signal.get("reasons", [])
    rsi = signal.get("rsi")
    current_price = signal.get("price")
    suggested_dollars = sizing.get("suggested_dollars", 0)
    suggested_shares = sizing.get("suggested_shares", 0)

    prompt = f"""You are a swing trading advisor recommending a new position for a busy MIT student investor.

INVESTOR PROFILE:
- Style: Swing to mid-term trader (weeks to a few months)
- Philosophy: Mean-reversion, Bollinger Bands, RSI 20-30 buy zone, 200MA filter
- Portfolio value: ~${portfolio_value:,.0f}
- Prefers quality companies, no penny stocks

OPPORTUNITY: {company_name} ({ticker}) — {sector}
- Current price: ${current_price:.2f}
- Signal tier: {final_tier}
- RSI: {rsi}
- Fundamental health: {fund_health}
- News: {news_verdict}
- Market context: {vix_note}

Technical signals firing:
{chr(10).join(f"✓ {r}" for r in tech_reasons) or "None"}

Suggested position: ${suggested_dollars:,.0f} ({suggested_shares} shares)

Generate a buy plan for this opportunity. Be direct and opinionated.

Respond in JSON:
{{
  "conviction": "high" | "medium" | "low",
  "buy_case": "2-3 sentence explanation of why this is a good swing opportunity right now",
  "entry_condition": "specific entry trigger (e.g. wait for RSI to reach 25 or price to touch lower BB)",
  "suggested_entry_price": <number or null>,
  "target_price": <number or null>,
  "exit_condition": "what signals a good exit",
  "timeframe": "estimated hold duration",
  "risk": "main risk to this trade",
  "priority": "high" | "medium" | "low"
}}"""

    try:
        response = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        result["ticker"] = ticker
        result["company_name"] = company_name
        result["price"] = current_price
        result["suggested_dollars"] = suggested_dollars
        result["suggested_shares"] = suggested_shares
        return result
    except Exception as e:
        return {
            "ticker": ticker,
            "company_name": company_name,
            "price": current_price,
            "conviction": "low",
            "buy_case": f"Analysis unavailable: {str(e)}",
            "entry_condition": "",
            "suggested_entry_price": None,
            "target_price": None,
            "exit_condition": "",
            "timeframe": "Unknown",
            "risk": "",
            "priority": "low",
            "suggested_dollars": suggested_dollars,
            "suggested_shares": suggested_shares,
        }
