import os
import json
import anthropic
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from src.fetcher import fetch_info, fetch_news

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
    event_opportunities: list[dict] = None,
) -> dict:
    """Generate a portfolio strategy with a conviction-gated priority action list."""

    # Holdings summary — include signal tier and scores so Claude has real data
    holdings_lines = []
    for p in position_plans:
        tier = p.get("final_tier", "Hold")
        rsi = p.get("rsi")
        buy_s = p.get("buy_score", 0)
        sell_s = p.get("sell_score", 0)
        score_str = f"buy={buy_s:.0%} sell={sell_s:.0%}"
        rsi_str = f" RSI={rsi}" if rsi else ""
        holdings_lines.append(
            f"- {p['ticker']}: signal={tier} | P&L {p.get('pnl_pct',0):+.1f}% | weight={p.get('portfolio_pct',0):.1f}%{rsi_str} | {score_str} | plan={p.get('action','?')} | {p.get('action_reason','')[:80]}"
        )
    holdings_summary = "\n".join(holdings_lines)

    # High-conviction opportunities (screener + event-driven, already filtered by caller)
    opp_lines = []
    for p in (opportunity_plans or [])[:5]:
        opp_lines.append(f"- {p['ticker']} [SCREENER]: {p.get('final_tier','Buy')} | {p.get('buy_case','')[:80]}")
    for t in (event_opportunities or [])[:5]:
        opp_lines.append(f"- {t['ticker']} [EVENT: {t.get('event_title','')}]: {t.get('signal_tier','Buy')} | {t.get('rationale','')[:80]}")
    opps_block = ("\nHIGH-CONVICTION NEW OPPORTUNITIES (already filtered — only strong signals):\n" + "\n".join(opp_lines)) if opp_lines else "\nNO NEW OPPORTUNITIES with strong conviction today."

    # Watchlist entries with active buy signals
    watchlist_lines = []
    for w in (watchlist or []):
        wt = w.get("latest_signal", {}).get("final_tier", "")
        if wt in ("Strong Buy", "Buy"):
            watchlist_lines.append(f"- {w['ticker']}: {wt} | {w.get('reason','')[:60]}")
    watchlist_block = ("\nWATCHLIST — BUY SIGNALS ACTIVE:\n" + "\n".join(watchlist_lines)) if watchlist_lines else ""

    vix = market_context.get("vix", {})
    fg = market_context.get("fear_greed", {})
    geo = market_context.get("geopolitical", {})
    flags = portfolio.get("flags", [])

    geo_line = ""
    if geo.get("risk_level") and geo["risk_level"] != "low":
        geo_line = f"\n- Geopolitical: {geo['risk_level'].upper()} — {geo.get('note', '')}"
        if geo.get("key_risks"):
            geo_line += f" | {'; '.join(geo['key_risks'][:2])}"

    prompt = f"""You are a swing trading advisor writing a daily strategy brief for a busy MIT student investor.

INVESTOR STYLE:
- Swing to mid-term trader (weeks to months), mean-reversion philosophy
- Buys oversold quality names, sells overbought — uses Bollinger Bands, RSI, MACD
- High conviction required: only acts on Strong Buy or Sell signals
- $1,000 swing budget remaining
- HOLD is the default. Most days nothing needs to be done.
- Never cuts losses on major drawdowns — holds through positions down >15% and lets them recover

PORTFOLIO: ${portfolio['total_value']:,.2f} total | P&L {portfolio['total_pnl_pct']:+.2f}% | Tech {portfolio['tech_pct']:.1f}% | Flags: {', '.join(flags) or 'none'}
MARKET: {vix.get('note','')} | F&G: {fg.get('label','N/A')} ({fg.get('value','N/A')}){geo_line}

CURRENT HOLDINGS:
{holdings_summary}
{opps_block}
{watchlist_block}

PRIORITY ACTION RULES — read carefully:
1. Only list an action if conviction is STRONG: signal must be "Strong Buy", "Buy", "Sell", or "Strong Sell"
2. HOLD is the default — do NOT put HOLD items on the list. If nothing is actionable, leave priority_actions empty.
3. An empty priority_actions list is completely fine and expected on most days.
4. New opportunities only appear if they are clearly better than doing nothing with current cash.
5. Actions must be specific: include share count, price level, or condition that triggers it.
6. NEVER recommend TRIM or EXIT on a position that is down more than 15% — hold through major losers and let them recover.

ACTION DEFINITIONS — use exactly these, nothing else:
- BUY: open or add to a position — Strong Buy signal
- ADD: add more to an existing position — Buy signal
- TRIM: partial exit, sell ~60% of shares held — Sell signal, only on profitable positions (P&L > 0%)
- EXIT: full position exit, sell all shares — Strong Sell signal, only on profitable positions or if thesis is completely broken

Respond in JSON:
{{
  "headline": "one sentence capturing today's portfolio posture",
  "market_stance": "offensive" | "defensive" | "neutral",
  "overall_thesis": "2-3 sentences on the next 4-8 week plan",
  "priority_actions": [
    {{
      "rank": 1,
      "action": "BUY" | "ADD" | "TRIM" | "EXIT",
      "ticker": "TICKER",
      "instruction": "specific actionable instruction with share count or price trigger",
      "urgency": "immediate" | "this week" | "this month"
    }}
  ],
  "biggest_risk": "single biggest risk right now",
  "what_to_watch": ["catalyst 1", "catalyst 2", "catalyst 3"],
  "on_course_check": ["condition plan is working", "condition that means revise plan"],
  "generated_at": "{datetime.now().isoformat()}"
}}"""

    try:
        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
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
        _save_portfolio_strategy(fallback)
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
            model="claude-haiku-4-5-20251001",
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
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
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


def generate_plan_with_news(
    holding: dict,
    signal: dict,
    fundamentals: dict,
    earnings: dict,
    market_context: dict,
    save: bool = True,
) -> dict:
    """Single Haiku call combining news analysis + position plan. Use in nightly scheduler."""
    ticker = holding["ticker"]
    news_items = fetch_news(ticker)
    info = fetch_info(ticker)
    company_name = info.get("longName", ticker)
    headlines = "\n".join(f"- {item.get('title','')}" for item in news_items[:10]) or "No recent news."

    vix_note = market_context.get("vix", {}).get("note", "")
    fg = market_context.get("fear_greed", {})
    geo = market_context.get("geopolitical", {})
    fund_health = fundamentals.get("health", "neutral")
    fund_flags = fundamentals.get("flags", [])
    earnings_status = earnings.get("verdict", {}).get("earnings_status", "unknown")
    days_to_earnings = earnings.get("verdict", {}).get("days_to_earnings")
    current_price = holding["current_price"]
    pnl_pct = holding["unrealized_pnl_pct"]

    geo_note = ""
    if geo.get("risk_level") and geo["risk_level"] != "low":
        geo_note = f" | Geopolitical: {geo['risk_level'].upper()} ({geo.get('note', '')})"

    prompt = f"""Swing trader analysis for {company_name} ({ticker}).

POSITION: {holding['shares']}sh @ ${holding['avg_cost']:.2f} | Now: ${current_price:.2f} | P&L: {pnl_pct:+.2f}% | Weight: {holding.get('portfolio_pct',0):.1f}%
TECHNICAL: {signal.get('tier','Hold')} | RSI: {signal.get('rsi','N/A')} | Firing: {', '.join(signal.get('reasons',[])[:3]) or 'none'}
FUNDAMENTALS: {fund_health}{(' | ' + ', '.join(fund_flags)) if fund_flags else ''}
EARNINGS: {earnings_status}{f' ({days_to_earnings}d)' if days_to_earnings else ''}
MARKET: {vix_note} | F&G: {fg.get('label','N/A')}{geo_note}

NEWS:
{headlines}

Philosophy: NEVER recommend trimming or exiting a position that is down more than 15% — hold through major losers and let them recover. Only exit profitable positions.

One JSON with news assessment + position plan:
{{"sentiment":"positive"|"neutral"|"negative","health":"healthy"|"neutral"|"deteriorating","key_events":["..."],"red_flags":[],"verdict":"one sentence","action":"Hold"|"Add More"|"Start Trimming"|"Exit"|"Wait","action_reason":"1-2 sentences","target_price":null,"exit_trigger":"specific condition","add_trigger":"specific condition","risk":"main risk","outlook":"short"|"neutral"|"cautious","timeframe":"e.g. 2-6 weeks"}}"""

    _news_fallback = {
        "ticker": ticker, "sentiment": "neutral", "health": "neutral",
        "key_events": [], "macro_context": "", "competitive_notes": "",
        "red_flags": [], "verdict": "", "confidence": "low",
    }
    _plan_fallback = {
        "ticker": ticker, "company_name": company_name, "action": "Hold",
        "action_reason": "Analysis unavailable.", "target_price": None,
        "target_reasoning": "", "exit_trigger": "", "add_trigger": "", "risk": "",
        "outlook": "neutral", "timeframe": "Unknown",
        "generated_at": datetime.now().isoformat(),
        "price_at_generation": current_price, "pnl_at_generation": pnl_pct,
    }

    try:
        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        r = json.loads(text.strip())

        news = {
            "ticker": ticker,
            "sentiment": r.get("sentiment", "neutral"),
            "health": r.get("health", "neutral"),
            "key_events": r.get("key_events", []),
            "macro_context": "", "competitive_notes": "",
            "red_flags": r.get("red_flags", []),
            "verdict": r.get("verdict", ""),
            "confidence": "medium",
        }
        plan = {
            "ticker": ticker, "company_name": company_name,
            "action": r.get("action", "Hold"),
            "action_reason": r.get("action_reason", ""),
            "target_price": r.get("target_price"),
            "target_reasoning": "",
            "exit_trigger": r.get("exit_trigger", ""),
            "add_trigger": r.get("add_trigger", ""),
            "risk": r.get("risk", ""),
            "outlook": r.get("outlook", "neutral"),
            "timeframe": r.get("timeframe", "Unknown"),
            "generated_at": datetime.now().isoformat(),
            "price_at_generation": current_price,
            "pnl_at_generation": pnl_pct,
        }
        if save:
            _save_plan(ticker, plan)
        return {"news": news, "plan": plan}
    except Exception as e:
        _plan_fallback["action_reason"] = f"Analysis unavailable: {e}"
        if save:
            _save_plan(ticker, _plan_fallback)
        return {"news": _news_fallback, "plan": _plan_fallback}
