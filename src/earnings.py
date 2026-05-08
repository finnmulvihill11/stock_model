import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from src.fetcher import fetch_info


def get_next_earnings(ticker: str) -> dict:
    info = fetch_info(ticker)
    earnings_ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")

    if not earnings_ts:
        return {"ticker": ticker, "next_date": None, "days_away": None, "status": "unknown"}

    next_date = datetime.fromtimestamp(earnings_ts).date()
    days_away = (next_date - datetime.now().date()).days

    if days_away < 0:
        status = "past"
    elif days_away <= 14:
        status = "imminent"
    elif days_away <= 30:
        status = "upcoming"
    else:
        status = "distant"

    return {
        "ticker": ticker,
        "next_date": str(next_date),
        "days_away": days_away,
        "status": status,
    }


def get_earnings_verdict(ticker: str) -> dict:
    """
    Returns a verdict on whether a post-earnings dip is a buy candidate.
    Beat/miss is inferred from price reaction + analyst surprise data.
    """
    t = yf.Ticker(ticker)
    info = fetch_info(ticker)

    eps_surprise = info.get("earningsForecastEPSSmartEstimate") or info.get("trailingEps")
    earnings_growth = info.get("earningsGrowth")
    revenue_growth = info.get("revenueGrowth")

    next_earnings = get_next_earnings(ticker)
    earnings_status = next_earnings["status"]

    # Assess based on available data
    positive_signals = 0
    negative_signals = 0
    notes = []

    if earnings_growth is not None:
        if earnings_growth > 0.05:
            positive_signals += 1
            notes.append(f"Earnings growing YoY (+{earnings_growth*100:.1f}%)")
        elif earnings_growth < -0.05:
            negative_signals += 1
            notes.append(f"Earnings declining YoY ({earnings_growth*100:.1f}%)")

    if revenue_growth is not None:
        if revenue_growth > 0:
            positive_signals += 1
            notes.append(f"Revenue growing (+{revenue_growth*100:.1f}%)")
        else:
            negative_signals += 1
            notes.append(f"Revenue declining ({revenue_growth*100:.1f}%)")

    if negative_signals > positive_signals:
        verdict = "avoid"
        reason = "Declining earnings/revenue — dip not recommended"
    elif positive_signals > 0:
        verdict = "consider"
        reason = "Fundamentals intact — dip may be an overreaction"
    else:
        verdict = "neutral"
        reason = "Insufficient earnings data to judge"

    return {
        "ticker": ticker,
        "verdict": verdict,
        "reason": reason,
        "notes": notes,
        "earnings_status": earnings_status,
        "next_earnings_date": next_earnings["next_date"],
        "days_to_earnings": next_earnings["days_away"],
    }


def earnings_gate(ticker: str) -> dict:
    """
    Main gate check. Returns whether to proceed with signal evaluation.
    """
    verdict = get_earnings_verdict(ticker)
    status = verdict["earnings_status"]

    if status == "imminent":
        return {
            "proceed": False,
            "reason": f"Earnings in {verdict['days_to_earnings']} days — wait for results before acting",
            "verdict": verdict,
        }

    if verdict["verdict"] == "avoid":
        return {
            "proceed": False,
            "reason": verdict["reason"],
            "verdict": verdict,
        }

    return {"proceed": True, "reason": verdict["reason"], "verdict": verdict}
