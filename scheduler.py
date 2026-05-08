"""
Overnight scheduler — runs Mon–Fri after market close via GitHub Actions.
Runs all analysis so the app is read-only in the morning.
"""
import yaml
from pathlib import Path
from datetime import datetime

from src.portfolio import get_portfolio
from src.signals import get_technical_signal
from src.fundamentals import check_fundamentals
from src.earnings import earnings_gate
from src.news import analyze_company
from src.market_context import get_market_context
from src.sizer import calculate_position_size
from src.budget import size_swing_trade, get_etf_dca_schedule
from src.alerts import send_daily_digest, send_strong_signal_alert
from src.planner import generate_position_plan, generate_portfolio_strategy
from src.etf_advisor import get_etf_universe, analyze_etf, generate_etf_plan
from src.screener import run_full_scrape, load_cache
from src.watchlist import refresh_watchlist_signals, get_watchlist, auto_add_strong_signals
from src.analysis_cache import (
    save_ticker_analysis, save_opportunity_plans,
    save_etf_universe_analysis, save_etf_opportunity_plans,
)
from src.planner import generate_opportunity_plan

CONFIG = yaml.safe_load(open(Path(__file__).parent / "config.yaml"))
TIER_ORDER = ["Strong Buy", "Buy", "Watch", "Hold", "Avoid", "Sell", "Strong Sell"]


def _final_tier(tech_tier: str, fund_health: str, news_sentiment: str, gate_proceed: bool) -> str:
    if fund_health == "deteriorating" or news_sentiment == "negative" or not gate_proceed:
        return "Avoid" if tech_tier in ("Strong Buy", "Buy") else tech_tier
    elif fund_health == "healthy" and news_sentiment == "positive":
        return "Strong Buy" if tech_tier == "Buy" else ("Strong Sell" if tech_tier == "Sell" else tech_tier)
    return tech_tier


def run():
    start = datetime.now()
    print(f"=== Overnight analysis started {start.strftime('%Y-%m-%d %H:%M')} ===\n")

    portfolio = get_portfolio()
    market = get_market_context()
    total_value = portfolio["total_value"]

    # ── 1. Full screener scrape ───────────────────────────────────────────────
    print("[ 1/8 ] Running full screener scrape...")
    try:
        run_full_scrape(verbose=True)
    except Exception as e:
        print(f"  Screener failed: {e}")

    # ── 2. Analyze all holdings ───────────────────────────────────────────────
    print("\n[ 2/8 ] Analyzing holdings...")
    signals = []
    position_plans = []

    for holding in portfolio["holdings"]:
        ticker = holding["ticker"]
        is_dca = holding.get("dca", False)
        print(f"  {ticker}...")
        try:
            sig = get_technical_signal(ticker)
            fund = check_fundamentals(ticker, high_risk=holding.get("high_risk", False))
            gate = earnings_gate(ticker)
            news_result = analyze_company(ticker)
            rs_data = {}
            try:
                from src.market_context import get_relative_strength
                rs_data = get_relative_strength(ticker)
            except Exception:
                pass

            tier = _final_tier(sig["tier"], fund["health"], news_result.get("sentiment", "neutral"), gate["proceed"])
            sig["final_tier"] = tier

            if not is_dca:
                sizing = size_swing_trade(ticker, sig["price"], sig.get("atr") or 1)
                sig["sizing"] = {"suggested_shares": sizing["shares"], "suggested_dollars": sizing["amount"], "note": sizing.get("note", "")}
            else:
                sched = get_etf_dca_schedule(sig["price"])
                sig["sizing"] = {"suggested_shares": sched["shares"], "suggested_dollars": sched["amount"], "note": sched["label"]}

            analysis = {
                "ticker": ticker,
                "signal": sig,
                "fundamentals": fund,
                "earnings": gate,
                "news": news_result,
                "relative_strength": rs_data,
                "final_tier": tier,
                "sizing": sig["sizing"],
                "is_dca": is_dca,
            }
            save_ticker_analysis(ticker, analysis)
            signals.append(sig)

            if not is_dca:
                plan = generate_position_plan(
                    holding=holding,
                    signal=sig,
                    fundamentals=fund,
                    news=news_result,
                    earnings=gate,
                    market_context=market,
                    final_tier=tier,
                )
                plan["final_tier"] = tier
                plan["pnl_pct"] = holding["unrealized_pnl_pct"]
                plan["current_price"] = holding["current_price"]
                plan["portfolio_pct"] = holding.get("portfolio_pct", 0)
                position_plans.append(plan)

            if tier in ("Strong Buy", "Strong Sell"):
                print(f"    ⚡ {tier} — sending alert")
                send_strong_signal_alert(sig)

        except Exception as e:
            print(f"  {ticker} failed: {e}")

    # ── 3. ETF plans ──────────────────────────────────────────────────────────
    print("\n[ 3/8 ] Generating ETF plans...")
    universe = get_etf_universe()
    dca_holdings = {h["ticker"]: h for h in portfolio["holdings"] if h.get("dca")}

    for ticker, holding in dca_holdings.items():
        print(f"  {ticker}...")
        try:
            meta = next((e for e in universe if e["ticker"] == ticker), {"name": ticker, "category": "ETF", "expense_ratio": 0})
            etf_analysis = analyze_etf(ticker, meta.get("expense_ratio", 0))
            generate_etf_plan(
                ticker=ticker,
                name=meta.get("name", ticker),
                category=meta.get("category", "ETF"),
                analysis=etf_analysis,
                market_context=market,
                is_held=True,
                holding=holding,
            )
        except Exception as e:
            print(f"  {ticker} ETF plan failed: {e}")

    # ── 4. Top opportunities from screener ────────────────────────────────────
    print("\n[ 4/8 ] Analyzing top opportunities...")
    portfolio_tickers = {h["ticker"] for h in portfolio["holdings"]}
    cache = load_cache()
    candidates = [r for r in cache.get("results", []) if r["ticker"] not in portfolio_tickers]
    top_candidates = candidates[:5]

    opp_plans = []
    for candidate in top_candidates:
        ticker = candidate["ticker"]
        print(f"  {ticker}...")
        try:
            fund = check_fundamentals(ticker)
            news_result = analyze_company(ticker)
            tier = _final_tier(candidate["tier"], fund["health"], news_result.get("sentiment", "neutral"), True)
            sizing = size_swing_trade(ticker, candidate["price"], candidate.get("atr") or 1)
            sized = {"suggested_dollars": sizing["amount"], "suggested_shares": sizing["shares"]}
            plan = generate_opportunity_plan(
                ticker=ticker,
                signal=candidate,
                fundamentals=fund,
                news=news_result,
                market_context=market,
                final_tier=tier,
                portfolio_value=total_value,
                sizing=sized,
            )
            plan["final_tier"] = tier
            opp_plans.append(plan)
        except Exception as e:
            print(f"  {ticker} opportunity failed: {e}")

    save_opportunity_plans(opp_plans)
    print(f"  Saved {len(opp_plans)} opportunity plans")

    # ── 5. Full ETF universe analysis ────────────────────────────────────────
    print("\n[ 5/8 ] Analyzing full ETF universe...")
    etf_universe_results = []
    for etf in universe:
        ticker = etf["ticker"]
        print(f"  {ticker}...")
        try:
            etf_analysis = analyze_etf(ticker, etf.get("expense_ratio", 0))
            etf_analysis["name"] = etf["name"]
            etf_analysis["category"] = etf["category"]
            etf_analysis["held"] = ticker in dca_holdings
            etf_universe_results.append(etf_analysis)
        except Exception as e:
            print(f"  {ticker} failed: {e}")
    save_etf_universe_analysis(etf_universe_results)
    print(f"  Saved {len(etf_universe_results)} ETF analyses")

    # ── 5b. New ETF opportunities ─────────────────────────────────────────────
    print("\n  Generating new ETF opportunity plans...")
    from src.etf_advisor import find_new_etf_opportunities
    new_etfs = find_new_etf_opportunities(list(dca_holdings.keys()))
    etf_opp_plans = []
    for etf in new_etfs:
        ticker = etf["ticker"]
        print(f"  {ticker}...")
        try:
            etf_a = next((r for r in etf_universe_results if r["ticker"] == ticker), None)
            if not etf_a:
                etf_a = analyze_etf(ticker, etf.get("expense_ratio", 0))
            plan = generate_etf_plan(
                ticker=ticker,
                name=etf["name"],
                category=etf["category"],
                analysis=etf_a,
                market_context=market,
                is_held=False,
            )
            plan["analysis"] = etf_a
            etf_opp_plans.append(plan)
        except Exception as e:
            print(f"  {ticker} ETF opp failed: {e}")
    etf_opp_plans.sort(key=lambda x: (0 if x.get("worth_adding") else 1))
    save_etf_opportunity_plans(etf_opp_plans)
    print(f"  Saved {len(etf_opp_plans)} ETF opportunity plans")

    # ── 6. Watchlist refresh ──────────────────────────────────────────────────
    print("\n[ 6/8 ] Refreshing watchlist...")
    try:
        refresh_watchlist_signals()
        screener_results = cache.get("results", [])
        auto_add_strong_signals(
            [r for r in screener_results if r["ticker"] not in portfolio_tickers],
            {r["ticker"] for r in screener_results if r.get("tier") in ("Strong Buy", "Buy")}
        )
    except Exception as e:
        print(f"  Watchlist refresh failed: {e}")

    # ── 6. Portfolio strategy ─────────────────────────────────────────────────
    print("\n[ 7/8 ] Generating portfolio strategy...")
    try:
        generate_portfolio_strategy(portfolio, position_plans, market, opp_plans, get_watchlist())
    except Exception as e:
        print(f"  Strategy failed: {e}")

    # ── 7. Daily digest email ─────────────────────────────────────────────────
    print("\n[ 8/8 ] Sending daily digest...")
    signals.sort(key=lambda x: TIER_ORDER.index(x.get("final_tier", "Hold")) if x.get("final_tier") in TIER_ORDER else 99)
    try:
        send_daily_digest(signals, portfolio, market)
    except Exception as e:
        print(f"  Digest failed: {e}")

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n=== Overnight analysis complete in {elapsed:.0f}s ===")


if __name__ == "__main__":
    run()
