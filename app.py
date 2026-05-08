import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yaml
from pathlib import Path
from datetime import datetime

from src.portfolio import get_portfolio
from src.signals import get_technical_signal
from src.market_context import get_market_context, get_relative_strength
from src.fundamentals import check_fundamentals
from src.earnings import earnings_gate
from src.news import analyze_company
from src.sizer import calculate_position_size
from src.budget import get_budget_state, get_swing_budget_remaining, get_etf_budget_remaining, size_swing_trade, get_etf_dca_schedule, record_allocation
from src.plans import get_plan, save_plan
from src.fetcher import fetch_ohlcv
from src.indicators import add_all_indicators
from src.analysis_cache import load_ticker_analysis, load_opportunity_plans, get_cache_status

CONFIG = yaml.safe_load(open(Path(__file__).parent / "config.yaml"))
B = CONFIG.get("budget", {})

st.set_page_config(page_title="Stock Model", layout="wide", page_icon="📈")

TIER_COLORS = {
    "Strong Buy": "#16a34a", "Buy": "#22c55e",
    "Watch": "#f59e0b", "Hold": "#6b7280",
    "Avoid": "#f97316", "Sell": "#ef4444", "Strong Sell": "#b91c1c",
}

# ── Sidebar navigation ────────────────────────────────────────────────────────
st.sidebar.title("Stock Model")
page = st.sidebar.radio("Navigation", [
    "Strategy Dashboard",
    "Signal Dashboard",
    "Ticker Detail",
    "Swing Trade Plans",
    "Long-Term ETF Plans",
])

# ── Helpers ───────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_portfolio():
    return get_portfolio()

@st.cache_data(ttl=3600)
def load_market_context():
    return get_market_context()

@st.cache_data(ttl=3600)
def load_signal(ticker):
    return get_technical_signal(ticker)

def load_full_analysis(ticker, high_risk=False, force_live=False):
    """Load analysis from overnight cache. Falls back to live only if forced (Ticker Detail)."""
    if not force_live:
        cached = load_ticker_analysis(ticker)
        if cached:
            return cached

    # Live fallback (Ticker Detail or cache miss)
    signal = get_technical_signal(ticker)
    fund = check_fundamentals(ticker, high_risk=high_risk)
    gate = earnings_gate(ticker)
    news = analyze_company(ticker)
    rs = get_relative_strength(ticker)
    budget_sizing = size_swing_trade(ticker, signal["price"], signal.get("atr") or 1)
    sizing = {"suggested_shares": budget_sizing["shares"], "suggested_dollars": budget_sizing["amount"], "note": budget_sizing.get("note", "")}
    signal["sizing"] = sizing
    tech_tier = signal["tier"]
    if fund["health"] == "deteriorating" or news.get("sentiment") == "negative" or not gate["proceed"]:
        final_tier = "Avoid" if tech_tier in ("Strong Buy", "Buy") else tech_tier
    elif fund["health"] == "healthy" and news.get("sentiment") == "positive":
        final_tier = "Strong Buy" if tech_tier == "Buy" else ("Strong Sell" if tech_tier == "Sell" else tech_tier)
    else:
        final_tier = tech_tier
    return {"signal": signal, "fundamentals": fund, "earnings": gate, "news": news, "relative_strength": rs, "sizing": sizing, "final_tier": final_tier}


def tier_badge(tier):
    color = TIER_COLORS.get(tier, "#6b7280")
    return f'<span style="background:{color};color:white;padding:3px 10px;border-radius:12px;font-weight:bold;font-size:14px">{tier}</span>'


# ── Page: Strategy Dashboard ──────────────────────────────────────────────────
if page == "Strategy Dashboard":
    st.title("Strategy Dashboard")

    portfolio = load_portfolio()
    market = load_market_context()

    col1, col2, col3, col4 = st.columns(4)
    pnl = portfolio["total_pnl"]
    col1.metric("Portfolio Value", f"${portfolio['total_value']:,.2f}")
    col2.metric("Total P&L", f"${pnl:+,.2f}", f"{portfolio['total_pnl_pct']:+.2f}%")
    col3.metric("Tech Concentration", f"{portfolio['tech_pct']:.1f}%",
                delta="⚠ Over limit" if portfolio['tech_pct'] > 70 else "OK",
                delta_color="inverse" if portfolio['tech_pct'] > 70 else "normal")

    vix = market["vix"]
    fg = market["fear_greed"]
    col4.metric("VIX", f"{vix['level']:.1f}", vix["sentiment"].upper())

    # Budget bar
    st.divider()
    budget = get_budget_state()
    st.subheader(f"3-Month Budget  ·  ends {budget['end_date']}  ·  {budget['months_remaining']} months remaining")
    bcol1, bcol2, bcol3, bcol4 = st.columns(4)
    bcol1.metric("Total Budget", f"${budget['total']:,.0f}")
    bcol2.metric("ETF Budget", f"${B.get('etf_budget', 1000):,.0f}", f"${get_etf_budget_remaining():,.0f} remaining")
    bcol3.metric("Swing Budget", f"${B.get('swing_budget', 1000):,.0f}", f"${get_swing_budget_remaining():,.0f} remaining")
    bcol4.metric("Total Spent", f"${budget['spent']:,.0f}", f"{budget['pct_used']:.1f}% used")
    st.progress(min(budget["pct_used"] / 100, 1.0))

    # Budget allocation history
    if budget["allocations"]:
        with st.expander("Budget allocation history"):
            alloc_df = pd.DataFrame(budget["allocations"])
            alloc_df.columns = [c.title() for c in alloc_df.columns]
            st.dataframe(alloc_df, use_container_width=True, hide_index=True)

            # Button to manually log a purchase
    with st.expander("Log a purchase"):
        with st.form("log_purchase"):
            lc1, lc2, lc3, lc4 = st.columns(4)
            log_ticker = lc1.text_input("Ticker").upper().strip()
            log_shares = lc2.number_input("Shares", min_value=1, step=1)
            log_price = lc3.number_input("Price per share ($)", min_value=0.01, step=0.01)
            log_type = lc4.selectbox("Type", ["swing", "etf"])
            if st.form_submit_button("Log Purchase"):
                record_allocation(log_ticker, int(log_shares), log_shares * log_price, log_type)
                st.success(f"Logged: {log_shares} shares of {log_ticker} @ ${log_price:.2f}")
                st.rerun()

    st.divider()

    # Holdings table
    st.subheader("Holdings")
    holdings_data = []
    for h in portfolio["holdings"]:
        holdings_data.append({
            "Ticker": h["ticker"],
            "Shares": h["shares"],
            "Avg Cost": f"${h['avg_cost']:.2f}",
            "Current": f"${h['current_price']:.2f}",
            "Value": f"${h['value']:,.2f}",
            "P&L": f"${h['unrealized_pnl']:+,.2f}",
            "P&L %": f"{h['unrealized_pnl_pct']:+.2f}%",
            "% of Portfolio": f"{h['portfolio_pct']:.1f}%",
        })
    st.dataframe(pd.DataFrame(holdings_data), use_container_width=True, hide_index=True)

    # Allocation pie
    col1, col2 = st.columns(2)
    with col1:
        fig = go.Figure(go.Pie(
            labels=[h["ticker"] for h in portfolio["holdings"]],
            values=[h["value"] for h in portfolio["holdings"]],
            hole=0.4,
        ))
        fig.update_layout(title="Portfolio Allocation", height=350, margin=dict(t=40, b=0))
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Flags")
        flags = portfolio.get("flags", [])
        if flags:
            for f in flags:
                st.warning(f)
        else:
            st.success("No concentration flags")

        st.subheader("Market Context")
        st.info(vix["note"])
        if fg["value"]:
            st.info(f"Fear & Greed: **{fg['label']}** ({fg['value']}/100)")


# ── Page: Signal Dashboard ────────────────────────────────────────────────────
elif page == "Signal Dashboard":
    st.title("Signal Dashboard")

    portfolio = load_portfolio()
    swing_tickers = CONFIG["portfolio"].get("swing_tickers", [])
    all_tickers = [h["ticker"] for h in portfolio["holdings"]]

    # Cache status
    status = get_cache_status(all_tickers)
    if status["status"] == "fresh":
        st.success(f"Overnight analysis ready · {status['message']}")
    elif status["status"] == "stale":
        st.warning(f"Data is stale · {status['message']}")
    elif status["status"] == "partial":
        st.warning(f"Partial data · {status['message']}")
    else:
        st.info("No overnight data yet — scheduler hasn't run. Showing live signals (free, no Claude).")

    # Auto-load from cache
    results = []
    for holding in portfolio["holdings"]:
        ticker = holding["ticker"]
        cached = load_ticker_analysis(ticker)
        if cached:
            cached["ticker"] = ticker
            results.append(cached)
        else:
            # Fast fallback — signals only, no Claude
            try:
                sig = get_technical_signal(ticker)
                results.append({"ticker": ticker, "signal": sig, "final_tier": sig["tier"], "sizing": {}, "fundamentals": {}, "news": {}, "earnings": {}})
            except Exception:
                pass

    if results:
        # Column headers
        h1, h2, h3, h4, h5 = st.columns([1.5, 1.5, 1, 1.5, 4])
        h1.markdown("**Ticker**")
        h2.markdown("**Signal**")
        h3.markdown("**Price**")
        h4.markdown("**Suggested Buy**")
        h5.markdown("**Why**")
        st.markdown("<hr style='margin:4px 0 8px 0'>", unsafe_allow_html=True)

        for r in sorted(results, key=lambda x: list(TIER_COLORS.keys()).index(x.get("final_tier","Hold")) if x.get("final_tier") in TIER_COLORS else 99):
            tier = r.get("final_tier", "Hold")
            sig = r.get("signal", {})
            sizing = r.get("sizing") or sig.get("sizing") or {}
            col1, col2, col3, col4, col5 = st.columns([1.5, 1.5, 1, 1.5, 4])
            col1.markdown(f"**{r['ticker']}**")
            col2.markdown(tier_badge(tier), unsafe_allow_html=True)
            col3.markdown(f"${sig.get('price', 0):,.2f}")
            if sizing and sizing.get("suggested_dollars", 0) > 0:
                col4.markdown(f"**${sizing['suggested_dollars']:,.0f}**<br><small>{sizing['suggested_shares']} shares</small>", unsafe_allow_html=True)
            else:
                col4.markdown("—")
            top_reasons = sig.get("reasons", [])[:3]
            col5.markdown(" · ".join(top_reasons) if top_reasons else "—")
            st.divider()
    else:
        st.info("Click **Run Analysis** to evaluate all holdings.")


# ── Page: Ticker Detail ───────────────────────────────────────────────────────
elif page == "Ticker Detail":
    st.title("Ticker Detail")

    ticker = st.text_input("Enter ticker", value="MSFT").upper().strip()

    if ticker and st.button("Analyze", type="primary"):
        portfolio = load_portfolio()
        holding = next((h for h in portfolio["holdings"] if h["ticker"] == ticker), None)
        high_risk = holding.get("high_risk", False) if holding else False

        with st.spinner(f"Running full analysis on {ticker}..."):
            analysis = load_full_analysis(ticker, high_risk)

        sig = analysis["signal"]
        fund = analysis["fundamentals"]
        news = analysis["news"]
        earnings = analysis["earnings"]
        rs = analysis["relative_strength"]
        final_tier = analysis["final_tier"]
        sizing = analysis["sizing"]

        # Header
        col1, col2, col3 = st.columns([2, 2, 3])
        col1.markdown(f"## {ticker}")
        col1.markdown(tier_badge(final_tier), unsafe_allow_html=True)
        col2.metric("Price", f"${sig['price']:,.2f}")
        col2.metric("RSI", f"{sig['rsi']}" if sig['rsi'] else "N/A")
        if sizing and sizing.get("suggested_dollars", 0) > 0:
            col3.metric("Suggested Buy", f"${sizing['suggested_dollars']:,.0f}",
                       f"{sizing['suggested_shares']} shares")
        st.divider()

        # Chart
        df = fetch_ohlcv(ticker)
        df = add_all_indicators(df)
        df_plot = df.tail(180)

        fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                            row_heights=[0.6, 0.2, 0.2],
                            subplot_titles=("Price + Indicators", "RSI", "MACD"))

        fig.add_trace(go.Candlestick(x=df_plot.index, open=df_plot["Open"],
            high=df_plot["High"], low=df_plot["Low"], close=df_plot["Close"], name="Price"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot["bb_upper"], line=dict(color="gray", dash="dot"), name="BB Upper"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot["bb_lower"], line=dict(color="gray", dash="dot"), name="BB Lower", fill="tonexty", fillcolor="rgba(128,128,128,0.05)"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot["ma50"], line=dict(color="blue", width=1), name="50MA"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot["ma200"], line=dict(color="orange", width=1), name="200MA"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot["rsi"], line=dict(color="purple"), name="RSI"), row=2, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=1)
        fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=1)
        colors = ["green" if v >= 0 else "red" for v in df_plot["macd_hist"]]
        fig.add_trace(go.Bar(x=df_plot.index, y=df_plot["macd_hist"], marker_color=colors, name="MACD Hist"), row=3, col=1)
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot["macd"], line=dict(color="blue"), name="MACD"), row=3, col=1)
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot["macd_signal"], line=dict(color="orange"), name="Signal"), row=3, col=1)
        fig.update_layout(height=600, showlegend=False, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

        # Two-pillar breakdown
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Technical Pillar")
            st.markdown(f"**Score:** {sig['buy_score'] if sig['direction']=='buy' else sig['sell_score']:.0%}")
            for r in sig.get("reasons", []):
                st.success(f"✓ {r}")
            for m in sig.get("misses", [])[:4]:
                st.error(f"✗ {m}")

        with col2:
            st.subheader("Fundamental & News Pillar")
            health_color = {"healthy": "🟢", "neutral": "🟡", "deteriorating": "🔴"}
            sentiment_color = {"positive": "🟢", "neutral": "🟡", "negative": "🔴"}
            st.markdown(f"**Company Health:** {health_color.get(fund['health'], '⚪')} {fund['health'].title()}")
            st.markdown(f"**News Sentiment:** {sentiment_color.get(news['sentiment'], '⚪')} {news['sentiment'].title()}")
            st.markdown(f"**Relative Strength (3m):** {rs.get('label', 'N/A').title()} ({rs.get('relative_strength', 'N/A')}%)")
            if earnings["verdict"]["earnings_status"] == "imminent":
                st.warning(f"⚠ Earnings in {earnings['verdict']['days_to_earnings']} days")
            st.info(f"**Claude verdict:** {news.get('verdict', '')}")
            if news.get("red_flags"):
                for flag in news["red_flags"]:
                    st.error(f"🚩 {flag}")
            for f in fund.get("flags", []):
                st.warning(f"⚠ {f}")


# ── Page: Swing Trade Plans ───────────────────────────────────────────────────
elif page == "Swing Trade Plans":
    from src.planner import (
        generate_position_plan, generate_opportunity_plan,
        generate_portfolio_strategy, get_current_plan, get_plan_history,
        get_portfolio_strategy, get_portfolio_strategy_history,
    )
    from src.sizer import calculate_position_size
    from datetime import datetime as dt

    st.title("Investment Plans")
    st.caption("Living strategy document — updated daily, persisted between sessions.")

    portfolio = load_portfolio()
    market = load_market_context()
    total_value = portfolio["total_value"]

    tab1, tab2, tab3 = st.tabs(["Portfolio Strategy", "My Positions", "New Opportunities"])

    # ── Tab 0: Portfolio-level strategy ──────────────────────────────────────
    with tab1:
        st.subheader("Overall Strategy")
        st.caption("The big picture — what you're trying to accomplish over the next 4-8 weeks.")

        strategy = get_portfolio_strategy()
        strategy_history = get_portfolio_strategy_history()

        if strategy:
            gen_at = strategy.get("generated_at", "")
            try:
                gen_dt = dt.fromisoformat(gen_at)
                age = (dt.now() - gen_dt).total_seconds() / 3600
                freshness = f"Last updated {gen_dt.strftime('%b %d at %I:%M %p')} ({age:.0f}h ago)"
            except Exception:
                freshness = "Last updated: unknown"

            stance_colors = {"offensive": "#16a34a", "defensive": "#b91c1c", "neutral": "#6b7280"}
            stance = strategy.get("market_stance", "neutral")
            stance_color = stance_colors.get(stance, "#6b7280")

            col1, col2 = st.columns([3, 1])
            col1.markdown(f"### {strategy.get('headline', '')}")
            col2.markdown(f"<div style='background:{stance_color};color:white;padding:6px 14px;border-radius:8px;font-weight:bold;text-align:center'>{stance.upper()}</div>", unsafe_allow_html=True)
            st.caption(freshness)
            st.divider()

            # ── Priority action list — shown first, most prominent ────────────
            priority_actions = strategy.get("priority_actions", [])
            if priority_actions:
                st.subheader("Priority Actions")
                st.caption("Ranked by urgency — work through this list top to bottom.")

                urgency_colors = {
                    "immediate": "#b91c1c",
                    "this week": "#f59e0b",
                    "this month": "#3b82f6",
                    "monitor": "#6b7280",
                }
                action_colors_map = {
                    "SELL": "#b91c1c", "EXIT": "#b91c1c", "TRIM": "#f59e0b",
                    "BUY": "#16a34a", "ADD": "#22c55e",
                    "WATCH": "#3b82f6", "HOLD": "#6b7280",
                }

                for item in sorted(priority_actions, key=lambda x: x.get("rank", 99)):
                    action = item.get("action", "HOLD").upper()
                    ticker = item.get("ticker", "")
                    instruction = item.get("instruction", "")
                    urgency = item.get("urgency", "monitor")
                    a_color = action_colors_map.get(action, "#6b7280")
                    u_color = urgency_colors.get(urgency, "#6b7280")
                    rank = item.get("rank", "—")

                    c1, c2, c3, c4 = st.columns([0.5, 1.2, 1.2, 4])
                    c1.markdown(f"**#{rank}**")
                    c2.markdown(f"<span style='background:{a_color};color:white;padding:3px 10px;border-radius:6px;font-weight:bold'>{action}</span>", unsafe_allow_html=True)
                    c3.markdown(f"**{ticker}**")
                    c4.markdown(f"{instruction} &nbsp; <span style='background:{u_color};color:white;padding:2px 8px;border-radius:10px;font-size:12px'>{urgency}</span>", unsafe_allow_html=True)

                st.divider()

            # ── Thesis + context ──────────────────────────────────────────────
            st.markdown(f"**Thesis:** {strategy.get('overall_thesis', '')}")

            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**What to Watch**")
                for w in strategy.get("what_to_watch", []):
                    st.markdown(f"👁 {w}")
                st.error(f"**Biggest Risk:** {strategy.get('biggest_risk', '')}")
            with col2:
                st.markdown("**Am I on course?**")
                checks = strategy.get("on_course_check", [])
                if len(checks) >= 2:
                    st.success(f"✓ On track if: {checks[0]}")
                    st.warning(f"⚠ Revise if: {checks[1]}")

            if strategy_history:
                with st.expander(f"Strategy history ({len(strategy_history)} previous versions)"):
                    for h in strategy_history[:3]:
                        archived = h.get("archived_at", "")
                        try:
                            archived_dt = dt.fromisoformat(archived)
                            label = archived_dt.strftime("%b %d")
                        except Exception:
                            label = "Previous"
                        prev_actions = h.get("priority_actions", [])
                        top = prev_actions[0].get("instruction", "") if prev_actions else ""
                        st.markdown(f"**{label}:** {h.get('headline', '')} *(stance: {h.get('market_stance', '?')})*")
                        if top:
                            st.caption(f"Top action was: {top}")
        else:
            st.info("No strategy generated yet. Generate position plans first, then the portfolio strategy will be built automatically.")

        if st.button("Regenerate Portfolio Strategy", key="regen_strategy"):
            saved_plans = []
            for h in portfolio["holdings"]:
                if not h.get("dca"):
                    p = get_current_plan(h["ticker"])
                    if p:
                        p["pnl_pct"] = h["unrealized_pnl_pct"]
                        saved_plans.append(p)
            if saved_plans:
                from src.watchlist import get_watchlist
                opp_plans = st.session_state.get("opportunity_plans", [])
                with st.spinner("Generating portfolio strategy..."):
                    strategy = generate_portfolio_strategy(portfolio, saved_plans, market, opp_plans, get_watchlist())
                st.rerun()
            else:
                st.warning("Generate position plans first.")

    # ── Tab 2: Current positions ──────────────────────────────────────────────
    with tab2:
        st.subheader("Position Plans")
        st.caption("Forward-looking plan for each holding — persists daily, updates automatically.")

        active_holdings = [h for h in portfolio["holdings"] if not h.get("dca")]

        col1, col2 = st.columns([3, 1])
        with col1:
            # Show staleness of oldest plan
            oldest = None
            for h in active_holdings:
                p = get_current_plan(h["ticker"])
                if p and p.get("generated_at"):
                    try:
                        age_h = (dt.now() - dt.fromisoformat(p["generated_at"])).total_seconds() / 3600
                        if oldest is None or age_h > oldest:
                            oldest = age_h
                    except Exception:
                        pass
            if oldest is not None:
                if oldest > 24:
                    st.warning(f"Plans are {oldest:.0f}h old — consider refreshing")
                else:
                    st.success(f"Plans last updated {oldest:.0f}h ago")
        with col2:
            refresh = st.button("Refresh All Plans", type="primary", key="gen_positions")

        if refresh:
            plans = []
            prog = st.progress(0)
            for i, holding in enumerate(active_holdings):
                ticker = holding["ticker"]
                with st.spinner(f"Planning {ticker}..."):
                    try:
                        analysis = load_full_analysis(ticker, holding.get("high_risk", False))
                        plan = generate_position_plan(
                            holding=holding,
                            signal=analysis["signal"],
                            fundamentals=analysis["fundamentals"],
                            news=analysis["news"],
                            earnings=analysis["earnings"],
                            market_context=market,
                            final_tier=analysis["final_tier"],
                        )
                        plan["final_tier"] = analysis["final_tier"]
                        plan["pnl_pct"] = holding["unrealized_pnl_pct"]
                        plan["current_price"] = holding["current_price"]
                        plan["portfolio_pct"] = holding.get("portfolio_pct", 0)
                        plans.append(plan)
                    except Exception as e:
                        st.warning(f"{ticker}: {e}")
                prog.progress((i + 1) / len(active_holdings))

            # Auto-generate portfolio strategy after refreshing positions
            from src.watchlist import get_watchlist
            opp_plans = st.session_state.get("opportunity_plans", [])
            with st.spinner("Updating portfolio strategy..."):
                generate_portfolio_strategy(portfolio, plans, market, opp_plans, get_watchlist())
            st.rerun()

        # Load persisted plans from disk
        plans = []
        for h in active_holdings:
            p = get_current_plan(h["ticker"])
            if p:
                p["pnl_pct"] = h["unrealized_pnl_pct"]
                p["current_price"] = h["current_price"]
                p["portfolio_pct"] = h.get("portfolio_pct", 0)
                plans.append(p)
        if plans:
            action_colors = {
                "Hold": "#6b7280", "Add More": "#16a34a",
                "Start Trimming": "#f59e0b", "Exit": "#b91c1c", "Wait": "#3b82f6",
            }
            outlook_icons = {"short": "📈", "neutral": "➡️", "cautious": "⚠️"}

            for p in plans:
                ticker = p["ticker"]
                action = p.get("action", "Hold")
                color = action_colors.get(action, "#6b7280")
                icon = outlook_icons.get(p.get("outlook", "neutral"), "➡️")
                pnl = p.get("pnl_pct", 0)
                pnl_str = f"{pnl:+.2f}%"

                # Check if action changed since yesterday
                history = get_plan_history(ticker)
                prev_action = history[0].get("action") if history else None
                changed = prev_action and prev_action != action
                change_tag = f"  *(was: {prev_action})*" if changed else ""

                # Freshness label
                gen_at = p.get("generated_at", "")
                try:
                    age_h = (dt.now() - dt.fromisoformat(gen_at)).total_seconds() / 3600
                    freshness = f"{age_h:.0f}h ago"
                except Exception:
                    freshness = ""

                with st.expander(
                    f"**{ticker}** — {p.get('company_name', '')}  |  {pnl_str}  |  {p.get('portfolio_pct', 0):.1f}%  |  updated {freshness}",
                    expanded=True
                ):
                    col1, col2, col3, col4 = st.columns([2, 2, 2, 2])
                    col1.markdown(f"<div style='background:{color};color:white;padding:8px 14px;border-radius:8px;font-weight:bold;font-size:16px;display:inline-block'>{action}</div>{change_tag}", unsafe_allow_html=True)
                    col2.markdown(f"**Timeframe:** {p.get('timeframe', '—')}")
                    col3.markdown(f"**Outlook:** {icon} {p.get('outlook', '—').title()}")
                    col4.markdown(f"**Price when planned:** ${p.get('price_at_generation', 0):,.2f}")

                    st.markdown(f"**Plan:** {p.get('action_reason', '')}")

                    col1, col2 = st.columns(2)
                    with col1:
                        if p.get("add_trigger"):
                            st.success(f"**Add more when:** {p['add_trigger']}")
                        if p.get("target_price"):
                            st.info(f"**Target:** ${p['target_price']:,.2f}  —  {p.get('target_reasoning', '')}")
                    with col2:
                        if p.get("exit_trigger"):
                            st.warning(f"**Exit when:** {p['exit_trigger']}")
                        if p.get("risk"):
                            st.error(f"**Risk:** {p['risk']}")

                    if history:
                        with st.expander(f"Plan history ({len(history)} versions)"):
                            for h in history[:5]:
                                try:
                                    h_dt = dt.fromisoformat(h.get("archived_at", ""))
                                    h_label = h_dt.strftime("%b %d")
                                except Exception:
                                    h_label = "Previous"
                                h_action = h.get("action", "?")
                                h_color = action_colors.get(h_action, "#6b7280")
                                st.markdown(f"**{h_label}** — <span style='color:{h_color};font-weight:bold'>{h_action}</span> @ ${h.get('price_at_generation', 0):,.2f} — {h.get('action_reason', '')[:100]}", unsafe_allow_html=True)
        else:
            st.info("Click **Refresh All Plans** to generate AI recommendations for your holdings. Plans persist and update daily.")

    # ── Tab 3: New opportunities ──────────────────────────────────────────────
    with tab3:
        from src.screener import load_cache

        st.subheader("New Opportunities")

        # Load from overnight cache
        opp_cache = load_opportunity_plans()
        screener_cache = load_cache()

        if opp_cache["saved_at"]:
            age = opp_cache.get("age_hours", 0)
            count = len(opp_cache.get("plans", []))
            if opp_cache["stale"]:
                st.warning(f"Opportunity plans are {age:.0f}h old — will refresh tonight. Showing last results.")
            else:
                st.success(f"Overnight analysis ready · {count} opportunities analyzed · {age:.0f}h ago")
        else:
            st.info("No overnight data yet. Once you push to GitHub and the scheduler runs, opportunities will appear here automatically every morning.")

        if screener_cache["generated_at"] and not opp_cache["saved_at"]:
            st.caption(f"Screener has {screener_cache['count']} raw candidates — overnight job will analyze the top ones tonight.")

        # Load opportunity plans from disk
        opp_plans = opp_cache.get("plans", [])
        st.session_state["opportunity_plans"] = opp_plans

        # ── Persistent Watchlist ──────────────────────────────────────────────
        from src.watchlist import (
            get_watchlist, add_to_watchlist, remove_from_watchlist,
            dismiss_from_watchlist, mark_acted, is_on_watchlist, get_urgency,
            get_signal_streak,
        )

        watched = get_watchlist()
        if watched:
            st.subheader("Your Watchlist")
            st.caption("Persisted between sessions — signals re-checked overnight.")
            urgency_order = {"URGENT": 0, "ACT SOON": 1, "MONITOR": 2, "STALE": 3}

            watched_sorted = sorted(watched, key=lambda x: urgency_order.get(
                get_urgency(x.get("latest_signal", {}), x.get("latest_signal", {}).get("final_tier", "Watch"))["level"], 99
            ))

            for entry in watched_sorted:
                ticker = entry["ticker"]
                sig = entry.get("latest_signal", {})
                final_tier = sig.get("final_tier", sig.get("tier", "Watch"))
                streak = entry.get("signal_streak", 0)
                urgency = get_urgency(sig, final_tier, streak)
                added = entry.get("added_date", "")
                last_updated = entry.get("last_updated", "")
                try:
                    updated_dt = dt.fromisoformat(last_updated)
                    updated_str = updated_dt.strftime("%b %d")
                except Exception:
                    updated_str = ""

                with st.container():
                    c1, c2, c3, c4, c5 = st.columns([1.5, 2, 2, 2, 2])
                    c1.markdown(f"**{ticker}**")
                    c2.markdown(f"<span style='background:{urgency['color']};color:white;padding:3px 10px;border-radius:6px;font-weight:bold;font-size:13px'>{urgency['level']}</span>", unsafe_allow_html=True)
                    c3.markdown(tier_badge(final_tier), unsafe_allow_html=True)
                    auto_tag = " · 🤖 auto" if entry.get("auto_added") else ""
                    streak_tag = f" · 🔥 {streak}d" if streak >= 2 else ""
                    c4.markdown(f"<small>Added {added} · checked {updated_str}{streak_tag}{auto_tag}</small>", unsafe_allow_html=True)
                    with c5:
                        bc1, bc2 = st.columns(2)
                        if bc1.button("Acted", key=f"acted_{ticker}"):
                            mark_acted(ticker)
                            st.rerun()
                        if bc2.button("Dismiss", key=f"dismiss_{ticker}"):
                            dismiss_from_watchlist(ticker)
                            st.rerun()
                    st.caption(urgency["label"])
                    if entry.get("reason"):
                        st.caption(f"Added because: {entry['reason']}")
                st.divider()

        st.subheader("New Opportunities")

        # ── Opportunity cards ─────────────────────────────────────────────────
        opp_plans = st.session_state.get("opportunity_plans", [])
        if opp_plans:
            conviction_colors = {"high": "#16a34a", "medium": "#f59e0b", "low": "#6b7280"}
            for p in opp_plans:
                ticker = p["ticker"]
                conviction = p.get("conviction", "low")
                color = conviction_colors.get(conviction, "#6b7280")
                on_watchlist = is_on_watchlist(ticker)

                with st.expander(
                    f"**{ticker}** — {p.get('company_name', '')}  |  ${p.get('price', 0):,.2f}  |  {conviction.upper()} CONVICTION"
                    + (" ★ Watching" if on_watchlist else ""),
                    expanded=True
                ):
                    col1, col2, col3, col4 = st.columns([2, 2, 2, 1.5])
                    col1.markdown(f"<div style='background:{color};color:white;padding:6px 12px;border-radius:8px;font-weight:bold;display:inline-block'>{conviction.upper()} CONVICTION</div>", unsafe_allow_html=True)
                    if p.get("suggested_dollars", 0) > 0:
                        col2.metric("Suggested Buy", f"${p['suggested_dollars']:,.0f}", f"{p['suggested_shares']} shares")
                    col3.markdown(f"**Timeframe:** {p.get('timeframe', '—')}")

                    # Urgency from signal
                    raw_signal = next((s for s in (st.session_state.get("signal_results") or []) if s.get("ticker") == ticker), {})
                    if not raw_signal:
                        raw_signal = {"tier": p.get("conviction", "low").title(), "buy_score": 0.7 if conviction == "high" else 0.5}
                    urgency = get_urgency(raw_signal, p.get("final_tier", "Buy" if conviction == "high" else "Watch"))
                    if urgency["level"] in ("URGENT", "ACT SOON"):
                        col4.markdown(f"<div style='background:{urgency['color']};color:white;padding:6px 10px;border-radius:8px;font-weight:bold;font-size:12px;text-align:center'>{urgency['level']}</div>", unsafe_allow_html=True)

                    st.markdown(f"**Why now:** {p.get('buy_case', '')}")

                    col1, col2 = st.columns(2)
                    with col1:
                        if p.get("entry_condition"):
                            st.success(f"**Entry trigger:** {p['entry_condition']}")
                        if p.get("suggested_entry_price"):
                            st.info(f"**Entry price target:** ${p['suggested_entry_price']:,.2f}")
                    with col2:
                        if p.get("target_price"):
                            st.info(f"**Price target:** ${p['target_price']:,.2f}")
                        if p.get("exit_condition"):
                            st.warning(f"**Exit when:** {p['exit_condition']}")
                        if p.get("risk"):
                            st.error(f"**Risk:** {p['risk']}")

                    # Watchlist button
                    if on_watchlist:
                        if st.button(f"Remove from Watchlist", key=f"rm_{ticker}"):
                            remove_from_watchlist(ticker)
                            st.rerun()
                    else:
                        if st.button(f"Add {ticker} to Watchlist", type="primary", key=f"add_{ticker}"):
                            add_to_watchlist(ticker, raw_signal or {"tier": "Buy"}, reason=p.get("buy_case", "")[:120])
                            st.success(f"{ticker} added to watchlist — signals will be tracked daily.")
                            st.rerun()
        else:
            st.info("Click **Analyze Top Opportunities** above to surface new swing candidates.")


# ── Page: Long-Term ETF Plans ──────────────────────────────────────────────────
elif page == "Long-Term ETF Plans":
    from src.etf_advisor import (
        get_etf_universe, analyze_etf, get_dca_suggestion,
        generate_etf_plan, get_etf_plan, find_new_etf_opportunities,
    )
    from datetime import date, datetime as dt

    st.title("Long-Term ETF Plans")
    st.caption("Accumulation strategy for your core ETF positions — separate from swing trades.")

    portfolio = load_portfolio()
    market = load_market_context()
    total_value = portfolio["total_value"]
    dca_holdings = {h["ticker"]: h for h in portfolio["holdings"] if h.get("dca")}
    held_etf_tickers = list(dca_holdings.keys())

    today = date.today()
    next_month = date(today.year + (today.month // 12), (today.month % 12) + 1, 1)

    tab1, tab2, tab3 = st.tabs(["My ETF Holdings", "Full ETF Universe", "New Additions"])

    rec_colors = {
        "accumulate": "#16a34a", "hold": "#6b7280", "reduce": "#f59e0b",
        "avoid": "#b91c1c", "consider_adding": "#3b82f6",
    }
    action_colors = {
        "accelerate": "#16a34a", "increase": "#22c55e", "normal": "#6b7280",
    }

    # ── Tab 1: Current ETF holdings ───────────────────────────────────────────
    with tab1:
        st.subheader("Your ETF Positions")

        col1, col2 = st.columns([3, 1])
        col1.markdown(f"Next monthly buy date: **{next_month.strftime('%B 1, %Y')}**")
        refresh_held = col2.button("Refresh Plans", type="primary", key="refresh_held_etfs")

        st.divider()

        for ticker, holding in dca_holdings.items():
            # Load or generate plan
            plan = get_etf_plan(ticker)
            if refresh_held or plan is None:
                universe = get_etf_universe()
                meta = next((e for e in universe if e["ticker"] == ticker), {"name": ticker, "category": "ETF", "expense_ratio": 0})
                with st.spinner(f"Analyzing {ticker}..."):
                    try:
                        analysis = analyze_etf(ticker, meta.get("expense_ratio", 0))
                        suggestion = get_dca_suggestion(ticker, analysis, total_value, holding["value"])
                        plan = generate_etf_plan(
                            ticker=ticker,
                            name=meta.get("name", ticker),
                            category=meta.get("category", "ETF"),
                            analysis=analysis,
                            market_context=market,
                            is_held=True,
                            holding=holding,
                        )
                        plan["analysis"] = analysis
                        plan["suggestion"] = suggestion
                    except Exception as e:
                        st.warning(f"{ticker}: {e}")
                        continue

            if plan is None:
                continue

            analysis = plan.get("analysis") or {}
            suggestion = plan.get("suggestion") or {}

            # If no analysis cached, recompute quick version
            if not analysis:
                try:
                    universe = get_etf_universe()
                    meta = next((e for e in universe if e["ticker"] == ticker), {"expense_ratio": 0})
                    analysis = analyze_etf(ticker, meta.get("expense_ratio", 0))
                    suggestion = get_dca_suggestion(ticker, analysis, total_value, holding["value"])
                except Exception:
                    analysis = {}
                    suggestion = {}

            rec = plan.get("recommendation", "hold")
            rec_color = rec_colors.get(rec, "#6b7280")
            dca_action = analysis.get("dca_action", "normal")
            dca_color = action_colors.get(dca_action, "#6b7280")

            with st.expander(f"**{ticker}** — {plan.get('name', ticker)}  |  P&L: {holding['unrealized_pnl_pct']:+.2f}%", expanded=True):
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Shares", holding["shares"])
                col1.metric("Avg Cost", f"${holding['avg_cost']:.2f}")
                col2.metric("Current Price", f"${holding['current_price']:.2f}")
                col2.metric("Value", f"${holding['value']:,.2f}")
                col3.metric("52W High", f"${analysis.get('high_52w', 0):,.2f}")
                col3.metric("Drawdown", f"{analysis.get('drawdown_from_high', 0):.1f}%")
                schedule = get_etf_dca_schedule(holding["current_price"])
                col4.markdown(f"**This month:**")
                if schedule["buy_this_month"]:
                    col4.markdown(f"<div style='background:{dca_color};color:white;padding:6px 12px;border-radius:8px;font-weight:bold;display:inline-block'>BUY {schedule['shares']} SHARE</div>", unsafe_allow_html=True)
                    col4.markdown(f"**${schedule['amount']:,.0f}** — {schedule['label']}")
                else:
                    col4.markdown(f"<div style='background:#6b7280;color:white;padding:6px 12px;border-radius:8px;font-weight:bold;display:inline-block'>SKIP THIS MONTH</div>", unsafe_allow_html=True)
                    col4.markdown(f"_{schedule['label']}_")

                st.markdown(f"**Assessment:** {plan.get('summary', '')}")
                st.info(f"**This month:** {plan.get('this_month', '')}")

                col1, col2 = st.columns(2)
                col1.markdown(f"**Long-term thesis:** {plan.get('long_term_thesis', '')}")
                if plan.get("risks"):
                    col2.warning(f"**Risk:** {plan['risks']}")

                # Price chart
                df_chart = fetch_ohlcv(ticker, period="2y")
                fig = go.Figure(go.Scatter(x=df_chart.index, y=df_chart["Close"], mode="lines",
                                           line=dict(color="#3b82f6"), name=ticker))
                fig.add_hline(y=holding["avg_cost"], line_dash="dash", line_color="orange",
                              annotation_text=f"Avg ${holding['avg_cost']:.2f}")
                if analysis.get("high_52w"):
                    ma200_vals = df_chart["Close"].rolling(200).mean()
                    fig.add_trace(go.Scatter(x=df_chart.index, y=ma200_vals,
                                             line=dict(color="gray", dash="dot"), name="200MA"))
                fig.update_layout(height=220, margin=dict(t=10, b=10), showlegend=False)
                st.plotly_chart(fig, use_container_width=True)
            st.divider()

    # ── Tab 2: Full ETF universe ──────────────────────────────────────────────
    with tab2:
        st.subheader("Full ETF Universe")
        st.caption("All 18 ETFs across every category — quick health check and DCA signal.")

        if st.button("Analyze Full Universe", type="primary", key="analyze_universe"):
            universe = get_etf_universe()
            universe_results = []
            prog = st.progress(0)
            for i, etf in enumerate(universe):
                with st.spinner(f"Analyzing {etf['ticker']}..."):
                    try:
                        analysis = analyze_etf(etf["ticker"], etf.get("expense_ratio", 0))
                        analysis["name"] = etf["name"]
                        analysis["category"] = etf["category"]
                        analysis["held"] = etf["ticker"] in held_etf_tickers
                        universe_results.append(analysis)
                    except Exception:
                        pass
                prog.progress((i + 1) / len(universe))
            st.session_state["universe_results"] = universe_results

        results = st.session_state.get("universe_results", [])
        if results:
            categories = sorted(set(r["category"] for r in results))
            for cat in categories:
                st.markdown(f"### {cat}")
                cat_results = [r for r in results if r["category"] == cat]
                rows = []
                for r in cat_results:
                    dca_color = action_colors.get(r["dca_action"], "#6b7280")
                    rows.append({
                        "Ticker": ("★ " if r["held"] else "") + r["ticker"],
                        "Name": r["name"][:35],
                        "Price": f"${r['current_price']:,.2f}",
                        "1Y Return": f"{r['ret_1y']:+.1f}%",
                        "Drawdown": f"{r['drawdown_from_high']:.1f}%",
                        "Trend": r["trend"].title(),
                        "ER": f"{r['expense_ratio']*100:.2f}%" if r['expense_ratio'] else "—",
                        "DCA Signal": r["dca_action"].upper(),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("Click **Analyze Full Universe** to see the complete ETF landscape.")

    # ── Tab 3: New additions ──────────────────────────────────────────────────
    with tab3:
        st.subheader("Worth Adding to Your DCA Stack?")
        st.caption("ETFs not currently in your portfolio that Claude recommends for long-term accumulation.")

        if st.button("Find ETF Opportunities", type="primary", key="find_etf_opps"):
            new_etfs = find_new_etf_opportunities(held_etf_tickers)
            opp_results = []
            prog = st.progress(0)
            for i, etf in enumerate(new_etfs):
                ticker = etf["ticker"]
                with st.spinner(f"Evaluating {ticker}..."):
                    try:
                        analysis = analyze_etf(ticker, etf.get("expense_ratio", 0))
                        plan = generate_etf_plan(
                            ticker=ticker,
                            name=etf["name"],
                            category=etf["category"],
                            analysis=analysis,
                            market_context=market,
                            is_held=False,
                        )
                        plan["analysis"] = analysis
                        opp_results.append(plan)
                    except Exception as e:
                        st.warning(f"{ticker}: {e}")
                prog.progress((i + 1) / len(new_etfs))

            opp_results.sort(key=lambda x: (0 if x.get("worth_adding") else 1,
                                             ["accumulate", "consider_adding", "hold", "reduce", "avoid"].index(
                                                 x.get("recommendation", "hold"))))
            st.session_state["etf_opp_results"] = opp_results

        opp_results = st.session_state.get("etf_opp_results", [])
        if opp_results:
            worth = [r for r in opp_results if r.get("worth_adding")]
            not_worth = [r for r in opp_results if not r.get("worth_adding")]

            if worth:
                st.markdown("#### Recommended Additions")
                for r in worth:
                    analysis = r.get("analysis", {})
                    rec_color = rec_colors.get(r.get("recommendation", "hold"), "#6b7280")
                    with st.expander(f"**{r['ticker']}** — {r.get('name', '')}  |  {r.get('category', '')}", expanded=True):
                        col1, col2, col3 = st.columns(3)
                        col1.markdown(f"<div style='background:{rec_color};color:white;padding:6px 12px;border-radius:8px;font-weight:bold;display:inline-block'>{r.get('recommendation','').upper()}</div>", unsafe_allow_html=True)
                        col2.metric("Price", f"${analysis.get('current_price', 0):,.2f}")
                        col3.metric("1Y Return", f"{analysis.get('ret_1y', 0):+.1f}%")
                        st.markdown(f"**Why add it:** {r.get('add_rationale', '')}")
                        st.markdown(f"**Long-term thesis:** {r.get('long_term_thesis', '')}")
                        if r.get("risks"):
                            st.warning(f"**Risk:** {r['risks']}")

            if not_worth:
                with st.expander(f"Not recommended right now ({len(not_worth)} ETFs)"):
                    for r in not_worth:
                        st.markdown(f"**{r['ticker']}** — {r.get('add_rationale', 'No strong case for adding now')}")
        else:
            st.info("Click **Find ETF Opportunities** to evaluate what else might be worth adding.")
