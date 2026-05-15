"""
Scheduler — three cadences, all run via GitHub Actions:
  run_nightly()  — Mon–Fri: swing holdings, plans, strategy, watchlist, digest
  run_weekly()   — Sunday:  screener scrape + new opportunity analysis
  run_monthly()  — 1st of month: full ETF universe + ETF plans
"""
import sys
import yaml
from pathlib import Path
from datetime import datetime

from src.portfolio import get_portfolio
from src.signals import get_technical_signal
from src.fundamentals import check_fundamentals
from src.earnings import earnings_gate
from src.news import analyze_company
from src.market_context import get_market_context
from src.budget import size_swing_trade, get_etf_dca_schedule
from src.alerts import send_daily_digest, send_strong_signal_alert, send_weekly_opportunities
from src.planner import generate_position_plan, generate_portfolio_strategy, generate_opportunity_plan
from src.etf_advisor import get_etf_universe, analyze_etf, generate_etf_plan, find_new_etf_opportunities
from src.screener import run_full_scrape, load_cache
from src.watchlist import refresh_watchlist_signals, get_watchlist, auto_add_strong_signals
from src.analysis_cache import (
    save_ticker_analysis, save_opportunity_plans,
    save_etf_universe_analysis, save_etf_opportunity_plans,
)
from src.market_context import get_relative_strength

CONFIG = yaml.safe_load(open(Path(__file__).parent / "config.yaml"))
TIER_ORDER = ["Strong Buy", "Buy", "Watch", "Hold", "Avoid", "Sell", "Strong Sell"]


def _final_tier(tech_tier, fund_health, news_sentiment, gate_proceed):
    if fund_health == "deteriorating" or news_sentiment == "negative" or not gate_proceed:
        return "Avoid" if tech_tier in ("Strong Buy", "Buy") else tech_tier
    elif fund_health == "healthy" and news_sentiment == "positive":
        return "Strong Buy" if tech_tier == "Buy" else ("Strong Sell" if tech_tier == "Sell" else tech_tier)
    return tech_tier


def run_nightly():
    """Mon–Fri: analyze current holdings only, generate position plans + portfolio strategy."""
    start = datetime.now()
    print(f"=== Nightly run started {start.strftime('%Y-%m-%d %H:%M')} ===\n")

    portfolio = get_portfolio()
    market = get_market_context()
    signals = []
    position_plans = []

    # ── Analyze all holdings ──────────────────────────────────────────────────
    print("[ 1/2 ] Analyzing holdings...")
    for holding in [h for h in portfolio["holdings"] if not h.get("dca")]:
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
                rs_data = get_relative_strength(ticker)
            except Exception:
                pass

            tier = _final_tier(sig["tier"], fund["health"], news_result.get("sentiment", "neutral"), gate["proceed"])
            sig["final_tier"] = tier

            if not is_dca:
                sz = size_swing_trade(ticker, sig["price"], sig.get("atr") or 1)
                sig["sizing"] = {"suggested_shares": sz["shares"], "suggested_dollars": sz["amount"], "note": sz.get("note", "")}
            else:
                sched = get_etf_dca_schedule(sig["price"])
                sig["sizing"] = {"suggested_shares": sched["shares"], "suggested_dollars": sched["amount"], "note": sched["label"]}

            save_ticker_analysis(ticker, {
                "ticker": ticker, "signal": sig, "fundamentals": fund,
                "earnings": gate, "news": news_result, "relative_strength": rs_data,
                "final_tier": tier, "sizing": sig["sizing"], "is_dca": is_dca,
            })
            signals.append(sig)

            if not is_dca:
                plan = generate_position_plan(
                    holding=holding, signal=sig, fundamentals=fund,
                    news=news_result, earnings=gate, market_context=market, final_tier=tier,
                )
                plan.update({"final_tier": tier, "pnl_pct": holding["unrealized_pnl_pct"],
                              "current_price": holding["current_price"], "portfolio_pct": holding.get("portfolio_pct", 0)})
                position_plans.append(plan)

        except Exception as e:
            print(f"  {ticker} failed: {e}")

    # ── Portfolio strategy (holdings only, no new opportunities) ─────────────
    print("\n[ 2/2 ] Generating portfolio strategy...")
    try:
        generate_portfolio_strategy(portfolio, position_plans, market, [], [])
    except Exception as e:
        print(f"  Strategy failed: {e}")

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n=== Nightly run complete in {elapsed:.0f}s ===")


def run_weekly():
    """Sunday: full screener scrape + analyze top new opportunities."""
    start = datetime.now()
    print(f"=== Weekly run started {start.strftime('%Y-%m-%d %H:%M')} ===\n")

    portfolio = get_portfolio()
    market = get_market_context()
    total_value = portfolio["total_value"]
    portfolio_tickers = {h["ticker"] for h in portfolio["holdings"]}

    # ── Full screener scrape ──────────────────────────────────────────────────
    print("[ 1/3 ] Running full screener scrape...")
    try:
        run_full_scrape(verbose=True)
    except Exception as e:
        print(f"  Screener failed: {e}")

    # ── Analyze top opportunities ─────────────────────────────────────────────
    print("\n[ 2/3 ] Analyzing top opportunities...")
    cache = load_cache()
    candidates = [r for r in cache.get("results", []) if r["ticker"] not in portfolio_tickers][:5]
    opp_plans = []

    for candidate in candidates:
        ticker = candidate["ticker"]
        print(f"  {ticker}...")
        try:
            fund = check_fundamentals(ticker)
            news_result = analyze_company(ticker)
            tier = _final_tier(candidate["tier"], fund["health"], news_result.get("sentiment", "neutral"), True)
            sz = size_swing_trade(ticker, candidate["price"], candidate.get("atr") or 1)
            plan = generate_opportunity_plan(
                ticker=ticker, signal=candidate, fundamentals=fund, news=news_result,
                market_context=market, final_tier=tier, portfolio_value=total_value,
                sizing={"suggested_dollars": sz["amount"], "suggested_shares": sz["shares"]},
            )
            plan["final_tier"] = tier
            opp_plans.append(plan)
        except Exception as e:
            print(f"  {ticker} failed: {e}")

    save_opportunity_plans(opp_plans)

    # ── Weekly opportunities email (disabled) ────────────────────────────────
    # try:
    #     send_weekly_opportunities(opp_plans, market)
    # except Exception as e:
    #     print(f"  Weekly email failed: {e}")

    # ── Watchlist refresh + auto-add ─────────────────────────────────────────
    print("\n[ 3/3 ] Refreshing watchlist...")
    try:
        refresh_watchlist_signals()
    except Exception as e:
        print(f"  Watchlist refresh failed: {e}")
    try:
        screener_results = cache.get("results", [])
        auto_add_strong_signals(
            [r for r in screener_results if r["ticker"] not in portfolio_tickers],
            {r["ticker"] for r in screener_results if r.get("tier") in ("Strong Buy", "Buy")}
        )
    except Exception as e:
        print(f"  Watchlist auto-add failed: {e}")

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n=== Weekly run complete in {elapsed:.0f}s ===")


def run_monthly():
    """1st of month: full ETF universe analysis + ETF opportunity plans."""
    start = datetime.now()
    print(f"=== Monthly ETF run started {start.strftime('%Y-%m-%d %H:%M')} ===\n")

    portfolio = get_portfolio()
    market = get_market_context()
    universe = get_etf_universe()
    dca_holdings = {h["ticker"]: h for h in portfolio["holdings"] if h.get("dca")}

    # ── Held ETF plans ────────────────────────────────────────────────────────
    print("[ 1/3 ] Generating held ETF plans...")
    for ticker, holding in dca_holdings.items():
        print(f"  {ticker}...")
        try:
            meta = next((e for e in universe if e["ticker"] == ticker), {"name": ticker, "category": "ETF", "expense_ratio": 0})
            etf_analysis = analyze_etf(ticker, meta.get("expense_ratio", 0))
            generate_etf_plan(
                ticker=ticker, name=meta.get("name", ticker), category=meta.get("category", "ETF"),
                analysis=etf_analysis, market_context=market, is_held=True, holding=holding,
            )
        except Exception as e:
            print(f"  {ticker} failed: {e}")

    # ── Full ETF universe ─────────────────────────────────────────────────────
    print("\n[ 2/3 ] Analyzing full ETF universe...")
    etf_universe_results = []
    for etf in universe:
        ticker = etf["ticker"]
        print(f"  {ticker}...")
        try:
            analysis = analyze_etf(ticker, etf.get("expense_ratio", 0))
            analysis.update({"name": etf["name"], "category": etf["category"], "held": ticker in dca_holdings})
            etf_universe_results.append(analysis)
        except Exception as e:
            print(f"  {ticker} failed: {e}")
    save_etf_universe_analysis(etf_universe_results)

    # ── New ETF opportunities ─────────────────────────────────────────────────
    print("\n[ 3/3 ] Generating new ETF opportunity plans...")
    new_etfs = find_new_etf_opportunities(list(dca_holdings.keys()))
    etf_opp_plans = []
    for etf in new_etfs:
        ticker = etf["ticker"]
        print(f"  {ticker}...")
        try:
            etf_a = next((r for r in etf_universe_results if r["ticker"] == ticker), None) or analyze_etf(ticker, etf.get("expense_ratio", 0))
            plan = generate_etf_plan(
                ticker=ticker, name=etf["name"], category=etf["category"],
                analysis=etf_a, market_context=market, is_held=False,
            )
            plan["analysis"] = etf_a
            etf_opp_plans.append(plan)
        except Exception as e:
            print(f"  {ticker} failed: {e}")

    etf_opp_plans.sort(key=lambda x: 0 if x.get("worth_adding") else 1)
    save_etf_opportunity_plans(etf_opp_plans)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n=== Monthly ETF run complete in {elapsed:.0f}s ===")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "nightly"
    if mode == "nightly":
        run_nightly()
    elif mode == "weekly":
        run_weekly()
    elif mode == "monthly":
        run_monthly()
    else:
        print(f"Unknown mode: {mode}. Use: nightly | weekly | monthly")
