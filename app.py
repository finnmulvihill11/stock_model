import subprocess
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yaml
from pathlib import Path
from datetime import datetime

from src.portfolio import get_portfolio
from src.signals import get_technical_signal
from src.market_context import get_market_context, get_relative_strength, get_geopolitical_context
from src.fundamentals import check_fundamentals
from src.earnings import earnings_gate
from src.news import analyze_company
from src.sizer import calculate_position_size
from src.budget import get_budget_state, get_swing_budget_remaining, get_etf_budget_remaining, size_swing_trade, get_etf_dca_schedule, record_allocation
from src.plans import get_plan, save_plan
from src.fetcher import fetch_ohlcv
from src.indicators import add_all_indicators
from src.analysis_cache import load_ticker_analysis, load_opportunity_plans
from src.github_sync import sync_budget, sync_watchlist, sync_config
from src.portfolio import update_holding_after_buy, update_holding_after_sell

CONFIG = yaml.safe_load(open(Path(__file__).parent / "config.yaml"))
B = CONFIG.get("budget", {})

st.set_page_config(page_title="Stock Model", layout="wide", page_icon="📈")


@st.cache_resource
def _pull_on_startup():
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=15)
        return result.stdout.strip() or "Already up to date."
    except Exception as e:
        return f"Pull failed: {e}"

_pull_on_startup()

TIER_COLORS = {
    "Strong Buy": "#16a34a", "Buy": "#22c55e",
    "Watch": "#f59e0b", "Hold": "#6b7280",
    "Avoid": "#f97316", "Sell": "#ef4444", "Strong Sell": "#b91c1c",
}

# ── Sidebar navigation ────────────────────────────────────────────────────────
st.sidebar.title("Stock Model")
page = st.sidebar.radio("Navigation", [
    "Strategy Dashboard",
    "Ticker Detail",
    "Swing Trade Plans",
    "Long-Term ETF Plans",
])

if st.sidebar.button("Sync latest data"):
    _pull_on_startup.clear()
    result = _pull_on_startup()
    if "Already up to date" in result:
        st.sidebar.success("Already up to date.")
    else:
        st.sidebar.success("Synced — reloading...")
        st.rerun()

# ── Helpers ───────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_portfolio():
    return get_portfolio()

@st.cache_data(ttl=3600)
def load_market_context():
    return get_market_context()

def load_full_analysis(ticker, high_risk=False, force_live=False, pnl_pct=None):
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
    geo = get_geopolitical_context()
    geo_risk = geo.get("risk_level", "low")
    if fund["health"] == "deteriorating" or news.get("sentiment") == "negative" or not gate["proceed"]:
        final_tier = "Avoid" if tech_tier in ("Strong Buy", "Buy") else tech_tier
    else:
        original_tier = tech_tier
        # Geo dampens buy-side conviction
        if geo_risk in ("high", "severe") and tech_tier == "Strong Buy":
            tech_tier = "Buy"
        if geo_risk == "severe" and tech_tier == "Buy":
            tech_tier = "Watch"
        # Geo amplifies sell-side when holding a profit
        in_profit = pnl_pct is not None and pnl_pct > 0
        if in_profit and geo_risk in ("high", "severe"):
            if original_tier == "Watch":
                tech_tier = "Sell"
            elif original_tier == "Sell" and geo_risk == "severe":
                tech_tier = "Strong Sell"
        if fund["health"] == "healthy" and news.get("sentiment") == "positive":
            final_tier = "Strong Buy" if tech_tier == "Buy" else ("Strong Sell" if tech_tier == "Sell" else tech_tier)
        else:
            final_tier = tech_tier
    return {"signal": signal, "fundamentals": fund, "earnings": gate, "news": news, "relative_strength": rs, "sizing": sizing, "final_tier": final_tier, "geopolitical": geo}


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
    bcol1.metric("Total Budget", f"${budget['total']:,.0f}", f"{budget['months_remaining']} months left")
    bcol2.metric("ETF Available", f"${get_etf_budget_remaining():,.0f}", f"of ${B.get('etf_budget', 1000):,.0f}")
    bcol3.metric("Swing Available", f"${get_swing_budget_remaining():,.0f}", f"of ${B.get('swing_budget', 1000):,.0f}")
    bcol4.metric("Net Spent", f"${budget['spent']:,.0f}", f"{budget['pct_used']:.1f}% of budget")
    st.progress(max(0.0, min(budget["pct_used"] / 100, 1.0)))

    # Budget allocation history
    if budget["allocations"]:
        with st.expander("Budget allocation history"):
            alloc_df = pd.DataFrame(budget["allocations"])
            alloc_df.columns = [c.title() for c in alloc_df.columns]
            st.dataframe(alloc_df, use_container_width=True, hide_index=True)

    # ── Log trades ────────────────────────────────────────────────────────────
    from src.budget import record_sell, get_sell_log, get_buy_log
    from src.plans import save_plan, get_plan

    col_buy, col_sell = st.columns(2)

    with col_buy:
        with st.expander("Log a purchase"):
            with st.form("log_purchase"):
                lc1, lc2, lc3, lc4 = st.columns(4)
                log_ticker = lc1.text_input("Ticker").upper().strip()
                log_shares = lc2.number_input("Shares", min_value=1, step=1)
                log_price = lc3.number_input("Price ($)", min_value=0.01, step=0.01)
                log_type = lc4.selectbox("Type", ["swing", "etf"])
                if st.form_submit_button("Log Buy"):
                    record_allocation(log_ticker, int(log_shares), log_shares * log_price, log_type)
                    update_holding_after_buy(log_ticker, int(log_shares), log_price,
                                             is_dca=(log_type == "etf"))
                    synced = sync_budget() and sync_config()
                    if synced:
                        st.success(f"Logged buy: {log_shares} shares of {log_ticker} @ ${log_price:.2f} — holdings updated and saved.")
                    else:
                        st.warning(f"Logged buy locally but GitHub sync failed — check GH_PAT secret.")
                    st.rerun()

    with col_sell:
        with st.expander("Log a sell"):
            with st.form("log_sell"):
                # Pre-fill avg cost from portfolio config
                holding_options = {h["ticker"]: h["avg_cost"] for h in portfolio["holdings"]}
                sc1, sc2, sc3 = st.columns(3)
                sell_ticker = sc1.text_input("Ticker").upper().strip()
                sell_shares = sc2.number_input("Shares sold", min_value=1, step=1)
                avg_cost_default = holding_options.get(sell_ticker, 0.0)
                sell_price = sc3.number_input("Sell price ($)", min_value=0.01, step=0.01)

                # Show avg cost lookup
                if sell_ticker in holding_options:
                    st.caption(f"Avg cost for {sell_ticker}: ${holding_options[sell_ticker]:.2f}")
                avg_cost_input = st.number_input("Avg cost per share ($)", min_value=0.01,
                                                  value=float(avg_cost_default) if avg_cost_default else 1.0, step=0.01)

                if st.form_submit_button("Log Sell", type="primary"):
                    result = record_sell(sell_ticker, int(sell_shares), sell_price, avg_cost_input)
                    holding_result = update_holding_after_sell(sell_ticker, int(sell_shares))
                    synced = sync_budget() and sync_config()
                    if not synced:
                        st.warning("Logged sell locally but GitHub sync failed — check GH_PAT secret.")
                    removed = holding_result.get("removed", False)
                    st.markdown(f"Logged sell: **{sell_shares} shares of {sell_ticker}** @ ${sell_price:.2f}")
                    st.markdown(f"Proceeds: **${result['proceeds']:,.2f}** returned to swing budget")
                    st.markdown(f"Realized P&L: **${result['realized_pnl']:+,.2f} ({result['realized_pnl_pct']:+.2f}%)**")
                    if removed:
                        st.info(f"{sell_ticker} fully exited — removed from holdings.")
                    plan = get_plan(sell_ticker)
                    plan["status"] = "closed" if removed else "holding"
                    save_plan(sell_ticker, plan)
                    st.rerun()

    # ── Buy log ───────────────────────────────────────────────────────────────
    buys = get_buy_log()
    if buys:
        st.subheader("Buy Log")
        buy_rows = []
        for b in sorted(buys, key=lambda x: x["date"], reverse=True):
            buy_rows.append({
                "Date": b["date"],
                "Ticker": b["ticker"],
                "Shares": b["shares"],
                "Price": f"${b['amount'] / b['shares']:,.2f}" if b["shares"] else "—",
                "Total": f"${b['amount']:,.2f}",
                "Type": b.get("type", "swing"),
            })
        st.dataframe(pd.DataFrame(buy_rows), use_container_width=True, hide_index=True)

    # ── Sell log ──────────────────────────────────────────────────────────────
    sells = get_sell_log()
    if sells:
        st.subheader("Sell Log")
        sell_rows = []
        for s in sorted(sells, key=lambda x: x["date"], reverse=True):
            pnl = s.get("realized_pnl", 0)
            sell_rows.append({
                "Date": s["date"],
                "Ticker": s["ticker"],
                "Shares": s["shares"],
                "Sell Price": f"${s.get('sell_price', 0):,.2f}",
                "Avg Cost": f"${s.get('avg_cost', 0):,.2f}",
                "Proceeds": f"${abs(s['amount']):,.2f}",
                "Realized P&L": f"${pnl:+,.2f}",
                "P&L %": f"{s.get('realized_pnl_pct', 0):+.2f}%",
            })
        st.dataframe(pd.DataFrame(sell_rows), use_container_width=True, hide_index=True)

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
        geo = market.get("geopolitical") or get_geopolitical_context()
        geo_risk = geo.get("risk_level", "low")
        geo_colors = {"low": "#16a34a", "moderate": "#f59e0b", "high": "#f97316", "severe": "#b91c1c"}
        geo_color = geo_colors.get(geo_risk, "#6b7280")
        st.markdown(
            f"Geopolitical Risk: <span style='background:{geo_color};color:white;padding:2px 10px;"
            f"border-radius:8px;font-weight:bold'>{geo_risk.upper()}</span>",
            unsafe_allow_html=True,
        )
        if geo.get("note") and "Unavailable" not in geo["note"]:
            st.caption(geo["note"])
        if geo.get("key_risks"):
            with st.expander("Key geopolitical risks"):
                for r in geo["key_risks"]:
                    st.markdown(f"• {r}")
                if geo.get("affected_sectors"):
                    st.caption(f"Affected sectors: {', '.join(geo['affected_sectors'])}")


# ── Page: Ticker Detail ───────────────────────────────────────────────────────
elif page == "Ticker Detail":
    st.title("Ticker Detail")

    ticker = st.text_input("Enter ticker", value="MSFT").upper().strip()

    if ticker and st.button("Analyze", type="primary"):
        portfolio = load_portfolio()
        holding = next((h for h in portfolio["holdings"] if h["ticker"] == ticker), None)
        high_risk = holding.get("high_risk", False) if holding else False
        pnl_pct = holding.get("unrealized_pnl_pct") if holding else None

        with st.spinner(f"Running full analysis on {ticker}..."):
            analysis = load_full_analysis(ticker, high_risk, pnl_pct=pnl_pct)

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
        if final_tier in ("Strong Buy", "Buy") and sizing and sizing.get("suggested_dollars", 0) > 0:
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
            # Technical verdict badge
            tech_direction = sig.get("direction", "buy")
            tech_score = sig["buy_score"] if tech_direction == "buy" else sig["sell_score"]
            tech_tier = sig.get("tier", "Hold")
            tech_color = TIER_COLORS.get(tech_tier, "#6b7280")
            st.markdown(
                f"**Technical Pillar** &nbsp; "
                f"<span style='background:{tech_color};color:white;padding:3px 12px;"
                f"border-radius:8px;font-weight:bold'>{tech_tier.upper()}</span> "
                f"<span style='color:#94a3b8;font-size:13px'>{tech_score:.0%} conditions met</span>",
                unsafe_allow_html=True
            )
            st.markdown("---")
            for r in sig.get("reasons", []):
                st.success(f"✓ {r}")
            for m in sig.get("misses", [])[:4]:
                st.error(f"✗ {m}")

        with col2:
            # Fundamental/news verdict badge
            fund_health = fund.get("health", "neutral")
            news_sentiment = news.get("sentiment", "neutral")
            fund_colors = {"healthy": "#16a34a", "neutral": "#6b7280", "deteriorating": "#b91c1c"}
            sent_colors = {"positive": "#16a34a", "neutral": "#6b7280", "negative": "#b91c1c"}
            fund_color = fund_colors.get(fund_health, "#6b7280")
            sent_color = sent_colors.get(news_sentiment, "#6b7280")
            st.markdown(
                f"**Fundamental & News Pillar** &nbsp; "
                f"<span style='background:{fund_color};color:white;padding:3px 12px;"
                f"border-radius:8px;font-weight:bold'>{fund_health.upper()}</span> &nbsp;"
                f"<span style='background:{sent_color};color:white;padding:3px 12px;"
                f"border-radius:8px;font-weight:bold'>{news_sentiment.upper()} NEWS</span>",
                unsafe_allow_html=True
            )
            st.markdown("---")
            rs_label = rs.get("label", "N/A").title()
            rs_val = rs.get("relative_strength", "N/A")
            st.markdown(f"**Relative Strength (3m):** {rs_label} ({rs_val}%)")
            days_to_earnings = earnings["verdict"].get("days_to_earnings")
            if days_to_earnings is not None and 0 <= days_to_earnings <= 3:
                st.error(f"EARNINGS IN {days_to_earnings} DAY{'S' if days_to_earnings != 1 else ''} — decide whether to hold or exit before results.")
            elif earnings["verdict"]["earnings_status"] == "imminent":
                st.warning(f"Earnings in {days_to_earnings} days")
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

            # Fallback: parse top_priorities into action items if priority_actions is empty
            if not priority_actions and strategy.get("top_priorities"):
                for i, p in enumerate(strategy["top_priorities"], 1):
                    upper = p.upper()
                    action = next((a for a in ["SELL","EXIT","TRIM","ADD","BUY","WATCH","HOLD"] if a in upper), "WATCH")
                    ticker = next((w for w in p.split() if w.isupper() and 2 <= len(w) <= 5 and w.isalpha()), "—")
                    priority_actions.append({"rank": i, "action": action, "ticker": ticker,
                                             "instruction": p, "urgency": "this week"})
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
                    ticker_pa = item.get("ticker", "")
                    instruction = item.get("instruction", "")
                    urgency = item.get("urgency", "monitor")
                    a_color = action_colors_map.get(action, "#6b7280")
                    u_color = urgency_colors.get(urgency, "#6b7280")
                    rank = item.get("rank", "—")

                    # Look up suggested amount for buy actions
                    amount_str = ""
                    if action in ("BUY", "ADD") and ticker_pa and ticker_pa != "—":
                        cached = load_ticker_analysis(ticker_pa)
                        if cached:
                            sz = cached.get("sizing") or cached.get("signal", {}).get("sizing", {})
                            if sz and sz.get("suggested_dollars", 0) > 0:
                                amount_str = f"&nbsp; <span style='color:#16a34a;font-weight:bold'>${sz['suggested_dollars']:,.0f} ({sz['suggested_shares']} shares)</span>"

                    c1, c2, c3, c4 = st.columns([0.5, 1.2, 1.2, 4])
                    c1.markdown(f"**#{rank}**")
                    c2.markdown(f"<span style='background:{a_color};color:white;padding:3px 10px;border-radius:6px;font-weight:bold'>{action}</span>", unsafe_allow_html=True)
                    c3.markdown(f"**{ticker_pa}**")
                    c4.markdown(f"{instruction}{amount_str} &nbsp; <span style='background:{u_color};color:white;padding:2px 8px;border-radius:10px;font-size:12px'>{urgency}</span>", unsafe_allow_html=True)

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
            st.info("Strategy will appear here after the first overnight run.")

    # ── Tab 2: Current positions ──────────────────────────────────────────────
    with tab2:
        st.subheader("Position Plans")
        st.caption("Forward-looking plan for each holding — updated overnight.")

        active_holdings = [h for h in portfolio["holdings"] if not h.get("dca")]

        # Freshness indicator
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
            if oldest > 25:
                st.warning(f"Plans are {oldest:.0f}h old — will refresh tonight.")
            else:
                st.success(f"Plans last updated {oldest:.0f}h ago.")

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
                            sync_watchlist()
                            st.rerun()
                        if bc2.button("Dismiss", key=f"dismiss_{ticker}"):
                            dismiss_from_watchlist(ticker)
                            sync_watchlist()
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
                            sync_watchlist()
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
        st.markdown(f"Next monthly buy date: **{next_month.strftime('%B 1, %Y')}**")
        st.divider()

        for ticker, holding in dca_holdings.items():
            plan = get_etf_plan(ticker)

            if plan is None:
                st.info(f"No overnight data for {ticker} yet — will appear after first scheduler run.")
                continue

            analysis = plan.get("analysis") or {}
            suggestion = plan.get("suggestion") or {}

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
        from src.analysis_cache import load_etf_universe_analysis
        st.subheader("Full ETF Universe")
        st.caption("All 16 ETFs across every category — updated overnight.")

        universe_cache = load_etf_universe_analysis()
        results = universe_cache.get("results", [])

        if results:
            age = universe_cache.get("age_hours", 0)
            st.success(f"Updated {age:.0f}h ago")
            categories = sorted(set(r["category"] for r in results))
            for cat in categories:
                st.markdown(f"### {cat}")
                cat_results = [r for r in results if r["category"] == cat]
                rows = []
                for r in cat_results:
                    rows.append({
                        "Ticker": ("★ " if r.get("held") else "") + r["ticker"],
                        "Name": r.get("name", r["ticker"])[:35],
                        "Price": f"${r['current_price']:,.2f}",
                        "1Y Return": f"{r['ret_1y']:+.1f}%",
                        "Drawdown": f"{r['drawdown_from_high']:.1f}%",
                        "Trend": r["trend"].title(),
                        "ER": f"{r['expense_ratio']*100:.2f}%" if r.get('expense_ratio') else "—",
                        "DCA Signal": r["dca_action"].upper(),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("ETF universe analysis will appear here after the first overnight run.")

    # ── Tab 3: New additions ──────────────────────────────────────────────────
    with tab3:
        from src.analysis_cache import load_etf_opportunity_plans
        st.subheader("Worth Adding to Your DCA Stack?")
        st.caption("ETFs not currently in your portfolio — analyzed overnight.")

        etf_opps = load_etf_opportunity_plans()
        opp_results = etf_opps.get("plans", [])

        if opp_results:
            age = etf_opps.get("age_hours", 0)
            st.success(f"Updated {age:.0f}h ago")
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
            st.info("ETF opportunity analysis will appear here after the first overnight run.")
