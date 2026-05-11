import json
from datetime import datetime, date
from pathlib import Path

WATCHLIST_FILE = Path(__file__).parent.parent / "data" / "watchlist.json"
WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    if WATCHLIST_FILE.exists():
        with open(WATCHLIST_FILE) as f:
            return json.load(f)
    return {"tickers": {}}


def _save(data: dict) -> None:
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_to_watchlist(ticker: str, signal: dict, reason: str = "") -> None:
    data = _load()
    existing = data["tickers"].get(ticker, {})
    history = existing.get("signal_history", [])

    # Archive previous signal snapshot
    if existing.get("latest_signal"):
        snap = existing["latest_signal"].copy()
        snap["recorded_at"] = existing.get("last_updated", "")
        history.insert(0, snap)
        history = history[:14]  # keep 2 weeks

    entry = {
        "ticker": ticker,
        "added_date": existing.get("added_date", str(date.today())),
        "last_updated": datetime.now().isoformat(),
        "reason": reason or existing.get("reason", ""),
        "latest_signal": signal,
        "signal_history": history,
        "status": "watching",  # watching | acted | dismissed
    }
    data["tickers"][ticker] = entry
    _save(data)


def remove_from_watchlist(ticker: str) -> None:
    data = _load()
    data["tickers"].pop(ticker, None)
    _save(data)


def dismiss_from_watchlist(ticker: str) -> None:
    data = _load()
    if ticker in data["tickers"]:
        data["tickers"][ticker]["status"] = "dismissed"
        _save(data)


def mark_acted(ticker: str) -> None:
    data = _load()
    if ticker in data["tickers"]:
        data["tickers"][ticker]["status"] = "acted"
        _save(data)


def get_watchlist() -> list[dict]:
    data = _load()
    return [v for v in data["tickers"].values() if v.get("status") == "watching"]


def get_all_watchlist() -> list[dict]:
    data = _load()
    return list(data["tickers"].values())


def is_on_watchlist(ticker: str) -> bool:
    data = _load()
    entry = data["tickers"].get(ticker)
    return entry is not None and entry.get("status") == "watching"


def get_urgency(signal: dict, final_tier: str, streak: int = 0) -> dict:
    """
    Determine urgency of a watchlist entry based on signal strength and streak.
    """
    rsi = signal.get("rsi")
    buy_score = signal.get("buy_score", 0)
    streak_note = f" · {streak}d streak" if streak >= 2 else ""

    if final_tier == "Strong Buy":
        if rsi and rsi <= 25:
            return {
                "level": "URGENT",
                "color": "#b91c1c",
                "label": f"Act now — all signals aligned, RSI deeply oversold{streak_note}",
            }
        return {
            "level": "URGENT",
            "color": "#b91c1c",
            "label": f"Strong Buy — all signals aligned{streak_note}",
        }
    elif final_tier == "Buy":
        if streak >= 3:
            return {
                "level": "URGENT",
                "color": "#b91c1c",
                "label": f"Buy signal persisting {streak} days — window closing{streak_note}",
            }
        if buy_score >= 0.75:
            return {
                "level": "ACT SOON",
                "color": "#f59e0b",
                "label": f"Buy signal — most conditions met{streak_note}",
            }
        return {
            "level": "ACT SOON",
            "color": "#f59e0b",
            "label": f"Buy signal firing{streak_note}",
        }
    elif final_tier == "Watch":
        return {
            "level": "MONITOR",
            "color": "#3b82f6",
            "label": f"Setting up — not ready yet{streak_note}",
        }
    else:
        return {
            "level": "STALE",
            "color": "#6b7280",
            "label": "Signal no longer active",
        }


def _compute_final_tier(tech_tier: str, ticker: str) -> tuple[str, dict, dict, dict]:
    """Run full analysis (fundamentals + earnings + news) and return final_tier + raw results."""
    from src.fundamentals import check_fundamentals
    from src.earnings import earnings_gate
    from src.news import analyze_company

    fund = check_fundamentals(ticker)
    gate = earnings_gate(ticker)
    news = analyze_company(ticker)

    if fund["health"] == "deteriorating" or news.get("sentiment") == "negative" or not gate["proceed"]:
        final_tier = "Avoid" if tech_tier in ("Strong Buy", "Buy") else tech_tier
    elif fund["health"] == "healthy" and news.get("sentiment") == "positive":
        final_tier = "Strong Buy" if tech_tier == "Buy" else tech_tier
    else:
        final_tier = tech_tier

    return final_tier, fund, gate, news


def auto_add_strong_signals(screener_results: list[dict], previous_tickers: set = None) -> list[str]:
    """
    Called by overnight scrape. Auto-adds tickers to watchlist based on signal strength.
    Runs full analysis (fundamentals + earnings + news) before adding so the watchlist
    final_tier matches what the signal dashboard shows.

    Rules:
    - Strong Buy (after full analysis) → add immediately
    - Buy (after full analysis) with buy_score >= 0.75 AND appeared in previous scrape → add
    - Already watched → update signal + streak regardless
    """
    previous_tickers = previous_tickers or set()
    auto_added = []

    for signal in screener_results:
        ticker = signal.get("ticker", "")
        tech_tier = signal.get("tier", "")
        buy_score = signal.get("buy_score", 0)
        already_watching = is_on_watchlist(ticker)

        # Run full analysis to get the real final_tier
        try:
            final_tier, fund, gate, news = _compute_final_tier(tech_tier, ticker)
        except Exception as e:
            print(f"  [{ticker}] full analysis failed, using tech tier: {e}")
            final_tier = tech_tier
            gate = {"proceed": True, "reason": ""}
            news = {}

        signal["final_tier"] = final_tier
        signal["earnings_gate"] = gate
        signal["news_sentiment"] = news.get("sentiment", "neutral")

        should_add = False
        reason = ""

        if final_tier == "Strong Buy":
            should_add = True
            reason = f"Strong Buy signal auto-detected by overnight scrape (score {buy_score:.0%})"
        elif final_tier == "Buy" and buy_score >= 0.75 and ticker in previous_tickers:
            should_add = True
            reason = f"Persistent Buy signal — active 2+ consecutive scrapes (score {buy_score:.0%})"
        elif already_watching:
            should_add = True
            reason = ""

        if should_add:
            data = _load()
            existing = data["tickers"].get(ticker, {})
            streak = existing.get("signal_streak", 0)

            if final_tier in ("Strong Buy", "Buy"):
                streak += 1
            else:
                streak = 0

            signal["signal_streak"] = streak
            add_to_watchlist(ticker, signal, reason or existing.get("reason", ""))

            data = _load()
            if ticker in data["tickers"]:
                data["tickers"][ticker]["signal_streak"] = streak
                if reason:
                    data["tickers"][ticker]["reason"] = reason
                    data["tickers"][ticker]["auto_added"] = True
            _save(data)

            if not already_watching and should_add and reason:
                auto_added.append(ticker)
                print(f"  Auto-added to watchlist: {ticker} ({final_tier}) — {reason[:60]}")

    return auto_added


def get_signal_streak(ticker: str) -> int:
    data = _load()
    return data["tickers"].get(ticker, {}).get("signal_streak", 0)


def refresh_watchlist_signals() -> list[dict]:
    """Re-run signals on all watched tickers. Called by overnight scheduler."""
    from src.signals import get_technical_signal
    from src.fundamentals import check_fundamentals
    from src.earnings import earnings_gate
    from src.news import analyze_company

    watched = get_watchlist()
    updated = []
    for entry in watched:
        ticker = entry["ticker"]
        try:
            signal = get_technical_signal(ticker)
            fund = check_fundamentals(ticker)
            gate = earnings_gate(ticker)
            news = analyze_company(ticker)

            tech_tier = signal["tier"]
            if fund["health"] == "deteriorating" or news.get("sentiment") == "negative" or not gate["proceed"]:
                final_tier = "Avoid" if tech_tier in ("Strong Buy", "Buy") else tech_tier
            elif fund["health"] == "healthy" and news.get("sentiment") == "positive":
                final_tier = "Strong Buy" if tech_tier == "Buy" else tech_tier
            else:
                final_tier = tech_tier

            signal["final_tier"] = final_tier
            add_to_watchlist(ticker, signal, entry.get("reason", ""))
            updated.append({"ticker": ticker, "tier": final_tier})
            print(f"  Watchlist refresh {ticker}: {final_tier}")
        except Exception as e:
            print(f"  Watchlist refresh {ticker}: error — {e}")

    return updated
