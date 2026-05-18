import os
import json
import anthropic
import pandas as pd
import yaml
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from src.fetcher import fetch_ohlcv, fetch_info

load_dotenv()
CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
EA = CONFIG["etf_advisor"]
PLANS_DIR = Path(__file__).parent.parent / "data" / "plans"
PLANS_DIR.mkdir(parents=True, exist_ok=True)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def get_etf_universe() -> list[dict]:
    universe = CONFIG.get("etf_universe", {})
    etfs = []
    for category, items in universe.items():
        for etf in items:
            etf["category"] = category.replace("_", " ").title()
            etfs.append(etf)
    return etfs


def analyze_etf(ticker: str, expense_ratio: float = None) -> dict:
    df = fetch_ohlcv(ticker, period="2y")
    info = fetch_info(ticker)

    current_price = float(df["Close"].iloc[-1])
    high_52w = float(df["Close"].tail(252).max())
    low_52w = float(df["Close"].tail(252).min())
    drawdown_from_high = (current_price - high_52w) / high_52w * 100
    position_in_range = (current_price - low_52w) / (high_52w - low_52w) * 100 if high_52w != low_52w else 50

    ma200 = float(df["Close"].rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
    ma50 = float(df["Close"].rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
    above_200ma = current_price > ma200 if ma200 else None

    ret_1m = float(df["Close"].pct_change(21).iloc[-1]) * 100
    ret_3m = float(df["Close"].pct_change(63).iloc[-1]) * 100
    ret_1y = float(df["Close"].pct_change(252).iloc[-1]) * 100

    # Trend health
    if above_200ma and ma50 and ma50 > ma200:
        trend = "strong uptrend"
    elif above_200ma:
        trend = "uptrend"
    elif ma200 and current_price > ma200 * 0.95:
        trend = "near 200MA"
    else:
        trend = "downtrend"

    # DCA recommendation
    if drawdown_from_high <= -EA["accelerate_drawdown_pct"]:
        dca_action = "accelerate"
        dca_multiplier = EA["accelerate_multiplier"]
        dca_note = f"Down {abs(drawdown_from_high):.1f}% from high — strong accumulation zone"
    elif drawdown_from_high <= -EA["normal_drawdown_pct"]:
        dca_action = "increase"
        dca_multiplier = EA["slight_increase_multiplier"]
        dca_note = f"Down {abs(drawdown_from_high):.1f}% from high — good time to add a bit more"
    else:
        dca_action = "normal"
        dca_multiplier = 1.0
        dca_note = "Near recent highs — stick to normal monthly amount"

    er = expense_ratio or info.get("annualReportExpenseRatio") or 0
    aum = info.get("totalAssets")
    aum_str = f"${aum/1e9:.1f}B" if aum and aum > 1e9 else (f"${aum/1e6:.0f}M" if aum else "N/A")

    return {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "drawdown_from_high": round(drawdown_from_high, 2),
        "position_in_range": round(position_in_range, 1),
        "above_200ma": above_200ma,
        "trend": trend,
        "ret_1m": round(ret_1m, 2),
        "ret_3m": round(ret_3m, 2),
        "ret_1y": round(ret_1y, 2),
        "expense_ratio": er,
        "aum": aum_str,
        "dca_action": dca_action,
        "dca_multiplier": dca_multiplier,
        "dca_note": dca_note,
    }


def get_dca_suggestion(
    ticker: str,
    analysis: dict,
    portfolio_value: float,
    current_holding_value: float = 0,
) -> dict:
    base_amount = portfolio_value * EA["normal_monthly_pct"]
    suggested = base_amount * analysis["dca_multiplier"]
    shares = int(suggested / analysis["current_price"]) if analysis["current_price"] > 0 else 0
    return {
        "ticker": ticker,
        "base_amount": round(base_amount, 2),
        "suggested_amount": round(suggested, 2),
        "suggested_shares": shares,
        "action": analysis["dca_action"],
        "note": analysis["dca_note"],
    }


def generate_etf_plan(
    ticker: str,
    name: str,
    category: str,
    analysis: dict,
    market_context: dict,
    is_held: bool,
    holding: dict = None,
) -> dict:
    vix_note = market_context.get("vix", {}).get("note", "")
    fg = market_context.get("fear_greed", {})

    holding_context = ""
    if is_held and holding:
        holding_context = f"""
Currently held: {holding.get('shares', 0)} shares @ ${holding.get('avg_cost', 0):.2f} avg cost
Unrealized P&L: {holding.get('unrealized_pnl_pct', 0):+.2f}%"""

    prompt = f"""You are a long-term ETF advisor for a swing/mid-term investor who also wants to build a core long-term ETF portfolio through dollar-cost averaging.

ETF: {name} ({ticker})
Category: {category}
{holding_context}

ANALYSIS:
- Current price: ${analysis['current_price']:.2f}
- 52-week range: ${analysis['low_52w']:.2f} – ${analysis['high_52w']:.2f}
- Drawdown from 52w high: {analysis['drawdown_from_high']:.1f}%
- Trend: {analysis['trend']}
- 1M return: {analysis['ret_1m']:+.1f}% | 3M: {analysis['ret_3m']:+.1f}% | 1Y: {analysis['ret_1y']:+.1f}%
- Above 200MA: {analysis['above_200ma']}
- Expense ratio: {analysis['expense_ratio']*100:.2f}% | AUM: {analysis['aum']}
- DCA signal: {analysis['dca_action'].upper()} — {analysis['dca_note']}

MARKET CONTEXT:
- {vix_note}
- Fear & Greed: {fg.get('label', 'N/A')} ({fg.get('value', 'N/A')})

{"This ETF is NOT currently in the portfolio." if not is_held else "This ETF IS currently held."}

Write a concise long-term ETF plan. Focus on accumulation strategy, not swing trading.

Respond in JSON:
{{
  "recommendation": "accumulate" | "hold" | "reduce" | "avoid" | "consider_adding",
  "summary": "2-3 sentence plain-English assessment of this ETF right now for long-term accumulation",
  "this_month": "specific advice for this month's contribution — how much relative to normal and why",
  "long_term_thesis": "1-2 sentences on why this ETF deserves a place in a long-term portfolio",
  "risks": "main risk or consideration for this ETF",
  "worth_adding": true | false,
  "add_rationale": "if not held, why or why not add it to the DCA stack",
  "generated_at": "{datetime.now().isoformat()}"
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
        result["name"] = name
        result["category"] = category
        result["generated_at"] = datetime.now().isoformat()
        _save_etf_plan(ticker, result)
        return result
    except Exception as e:
        fallback = {
            "ticker": ticker,
            "name": name,
            "category": category,
            "recommendation": "hold",
            "summary": f"Analysis unavailable: {str(e)}",
            "this_month": "Stick to normal amount",
            "long_term_thesis": "",
            "risks": "",
            "worth_adding": False,
            "add_rationale": "",
            "generated_at": datetime.now().isoformat(),
        }
        _save_etf_plan(ticker, fallback)
        return fallback


def _save_etf_plan(ticker: str, plan: dict) -> None:
    path = PLANS_DIR / f"ETF_{ticker.upper()}.json"
    existing = {}
    if path.exists():
        with open(path) as f:
            existing = json.load(f)
    history = existing.get("history", [])
    if existing.get("current"):
        snap = existing["current"].copy()
        snap["archived_at"] = datetime.now().isoformat()
        history.insert(0, snap)
        history = history[:7]
    with open(path, "w") as f:
        json.dump({"current": plan, "history": history}, f, indent=2)


def get_etf_plan(ticker: str) -> dict | None:
    path = PLANS_DIR / f"ETF_{ticker.upper()}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f).get("current")
    return None


def find_new_etf_opportunities(held_tickers: list[str]) -> list[dict]:
    universe = get_etf_universe()
    return [e for e in universe if e["ticker"] not in held_tickers]
