import json
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from src.fetcher import fetch_ohlcv
from src.indicators import add_all_indicators
from src.divergence import detect_rsi_divergence
from src.signals import get_technical_signal
from src.fundamentals import check_fundamentals
from src.earnings import earnings_gate
from src.news import analyze_company
from src.market_context import get_relative_strength
from src.budget import size_swing_trade
from src.tier import _final_tier
from src.planner import generate_plan_with_news, generate_opportunity_plan

DB_PATH = Path(__file__).parent.parent / "data" / "virtual_trader.db"


def _init_db(db_path: Path = None) -> None:
    path = str(db_path or DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS virtual_trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker        TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'open',
                entry_date    TEXT NOT NULL,
                entry_price   REAL NOT NULL,
                entry_tier    TEXT NOT NULL,
                entry_metrics TEXT NOT NULL,
                exit_date     TEXT,
                exit_price    REAL,
                exit_tier     TEXT,
                exit_metrics  TEXT,
                return_pct    REAL,
                holding_days  INTEGER
            )
        """)
        conn.commit()
    finally:
        conn.close()


_init_db()


def open_position(
    ticker: str,
    entry_date: str,
    entry_price: float,
    entry_tier: str,
    entry_metrics: str,
    db_path: Path = None,
) -> int:
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute(
            """INSERT INTO virtual_trades
               (ticker, status, entry_date, entry_price, entry_tier, entry_metrics)
               VALUES (?, 'open', ?, ?, ?, ?)""",
            (ticker, entry_date, entry_price, entry_tier, entry_metrics),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def close_open_positions_for_ticker(
    ticker: str,
    exit_date: str,
    exit_price: float,
    exit_tier: str,
    exit_metrics: str,
    db_path: Path = None,
) -> int:
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT id, entry_price, entry_date FROM virtual_trades WHERE ticker=? AND status='open'",
            (ticker,),
        ).fetchall()
        for row_id, entry_price, entry_date_str in rows:
            return_pct = (exit_price - entry_price) / entry_price
            holding_days = (date.fromisoformat(exit_date) - date.fromisoformat(entry_date_str)).days
            conn.execute(
                """UPDATE virtual_trades SET
                   status='closed', exit_date=?, exit_price=?, exit_tier=?,
                   exit_metrics=?, return_pct=?, holding_days=?
                   WHERE id=?""",
                (exit_date, exit_price, exit_tier, exit_metrics,
                 round(return_pct, 6), holding_days, row_id),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_open_tickers(db_path: Path = None) -> list[str]:
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM virtual_trades WHERE status='open'"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_open_position(ticker: str, db_path: Path = None) -> dict | None:
    """Latest open position for a ticker, or None. Used to reconstruct a
    synthetic 'holding' (entry price as cost basis) for exit decisions."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            """SELECT entry_price, entry_date, entry_metrics FROM virtual_trades
               WHERE ticker=? AND status='open' ORDER BY id DESC LIMIT 1""",
            (ticker,),
        ).fetchone()
        if not row:
            return None
        entry_price, entry_date, entry_metrics = row
        return {"entry_price": entry_price, "entry_date": entry_date, "entry_metrics": entry_metrics}
    finally:
        conn.close()


def build_metric_snapshot(
    ticker: str,
    df: "pd.DataFrame",
    div: dict,
    tech_sig: dict,
    fund: dict,
    gate: dict,
    news: dict,
    rs_data: dict,
    market: dict,
    final_tier: str,
    sz: dict,
    plan: dict = None,
) -> str:
    """Assemble a complete metric snapshot from all analysis components. Returns JSON string."""

    def _f(val):
        try:
            return round(float(val), 6) if pd.notna(val) else None
        except (TypeError, ValueError):
            return None

    row = df.iloc[-1]

    snapshot = {
        # Stock identifier (required for ML dataset grouping)
        "ticker":       ticker,
        # Raw indicator values (features for ML)
        "price":        _f(row["Close"]),
        "rsi":          _f(row["rsi"]),
        "bb_pct":       _f(row["bb_pct"]),
        "bb_width":     _f(row["bb_width"]),
        "bb_upper":     _f(row["bb_upper"]),
        "bb_mid":       _f(row["bb_mid"]),
        "bb_lower":     _f(row["bb_lower"]),
        "macd":         _f(row["macd"]),
        "macd_signal":  _f(row["macd_signal"]),
        "macd_hist":    _f(row["macd_hist"]),
        "ma50":         _f(row["ma50"]),
        "ma200":        _f(row["ma200"]),
        "atr":          _f(row["atr"]),
        "volume_ratio": _f(row["volume_ratio"]),
        # Technical signal
        "buy_score":      _f(tech_sig["buy_score"]),
        "sell_score":     _f(tech_sig["sell_score"]),
        "technical_tier": tech_sig["tier"],
        "direction":      tech_sig["direction"],
        "reasons":        tech_sig.get("reasons", []),
        "misses":         tech_sig.get("misses", []),
        # RSI divergence
        "rsi_div_bullish":     div["bullish"],
        "rsi_div_bearish":     div["bearish"],
        "rsi_div_bull_reason": div.get("bull_reason", ""),
        "rsi_div_bear_reason": div.get("bear_reason", ""),
        # Market context
        "vix_level":        _f(market.get("vix", {}).get("level")),
        "vix_sentiment":    market.get("vix", {}).get("sentiment"),
        "fear_greed_value": _f(market.get("fear_greed", {}).get("value")),
        "fear_greed_label": market.get("fear_greed", {}).get("label"),
        "geo_risk":         market.get("geopolitical", {}).get("risk_level", "low"),
        "relative_strength": _f(rs_data.get("relative_strength")) if rs_data else None,
        "rs_label":          rs_data.get("label") if rs_data else None,
        # Fundamentals
        "fund_health":         fund["health"],
        "fund_revenue_growth": _f(fund.get("revenue_growth")),
        "fund_de_ratio":       _f(fund.get("de_ratio")),
        "fund_profit_margins": _f(fund.get("profit_margins")),
        "fund_passed":         fund.get("passed", []),
        "fund_flags":          fund.get("flags", []),
        # Earnings gate
        "earnings_proceed": gate["proceed"],
        "earnings_reason":  gate.get("reason", ""),
        "earnings_verdict": gate.get("verdict", {}),
        # News / Claude
        "news_sentiment":  news.get("sentiment"),
        "news_health":     news.get("health"),
        "news_key_events": news.get("key_events", []),
        "news_red_flags":  news.get("red_flags", []),
        "news_confidence": news.get("confidence"),
        "news_verdict":    news.get("verdict", ""),
        # Final decision
        "final_tier":        final_tier,
        "suggested_shares":  sz.get("shares"),
        "suggested_dollars": sz.get("amount"),
        # Position plan — same Claude call the real pipeline uses (generate_plan_with_news
        # for exits: action/action_reason; generate_opportunity_plan for entries:
        # conviction/buy_case/entry_condition/etc). Stored raw since the shape differs.
        "plan": plan or {},
    }
    return json.dumps(snapshot)


def run_virtual_entries(cache: dict, market: dict, geo_risk: str, db_path: Path = None) -> None:
    """
    Weekly: open a virtual position for every ticker where the full two-pillar
    analysis produces Strong Buy or Buy. Covers the entire screener universe
    (not just the top 5 the user-facing flow sees) and writes to virtual_trades.
    """
    today = str(date.today())
    candidates = [r for r in cache.get("results", []) if r.get("tier") in ("Strong Buy", "Buy")]
    print(f"[VT] Weekly entries: {len(candidates)} technical buy candidates")

    already_open = set(get_open_tickers(db_path=db_path))

    for candidate in candidates:
        ticker = candidate["ticker"]
        if ticker in already_open:
            print(f"[VT]   {ticker} already has an open position — skipping duplicate entry")
            continue
        try:
            df = fetch_ohlcv(ticker)
            df = add_all_indicators(df)
            div = detect_rsi_divergence(df)
            tech_sig = get_technical_signal(ticker)
            fund = check_fundamentals(ticker)
            gate = earnings_gate(ticker)
            news = analyze_company(ticker)
            rs_data = {}
            try:
                rs_data = get_relative_strength(ticker)
            except Exception:
                pass

            # Use the screener's cached technical tier (Pillar 1) — not a fresh live
            # recompute — so the entry decision matches the signal that triggered screening.
            tier = _final_tier(
                candidate["tier"], fund["health"],
                news.get("sentiment", "neutral"), gate["proceed"], geo_risk,
            )
            if tier not in ("Strong Buy", "Buy"):
                print(f"[VT]   {ticker}: Pillar 2 downgraded to {tier} — skipping")
                continue

            # Use df close/ATR directly — tech_sig["price"] can be NaN if the signal
            # module had insufficient data to compute all indicators. Drop NaN rows
            # (yfinance may append a partial/empty row for the current day).
            clean = df.dropna(subset=["Close"])
            row = clean.iloc[-1]
            entry_price = float(row["Close"])
            entry_atr = float(row["atr"]) if pd.notna(row.get("atr")) else 1.0

            sz = size_swing_trade(
                ticker, entry_price, entry_atr,
                tier=tier, portfolio_value=0, current_position_value=0,
            )
            # Same buy-plan generation the real weekly pipeline runs for new
            # opportunities (generate_opportunity_plan) — folds the news verdict
            # into a conviction/buy-case narrative rather than just gating on tier.
            plan = generate_opportunity_plan(
                ticker=ticker, signal=candidate, fundamentals=fund, news=news,
                market_context=market, final_tier=tier, portfolio_value=0,
                sizing={"suggested_dollars": sz["amount"], "suggested_shares": sz["shares"]},
            )
            metrics_json = build_metric_snapshot(
                ticker, df, div, tech_sig, fund, gate, news, rs_data, market, tier, sz, plan=plan
            )
            open_position(ticker, today, entry_price, tier, metrics_json, db_path=db_path)
            print(f"[VT]   Opened {ticker} @ ${entry_price:.2f} ({tier})")
        except Exception as e:
            print(f"[VT]   {ticker} entry failed: {e}")


def run_virtual_exits(market: dict, geo_risk: str, db_path: Path = None) -> None:
    """
    Nightly: check every open virtual position using the exact same exit
    decision the real pipeline uses for holdings — the Claude-generated plan
    action from generate_plan_with_news (Hold / Add More / Start Trimming /
    Exit / Wait) — not a raw technical tier. A virtual position closes fully
    on "Start Trimming" or "Exit" since virtual_trades has no partial-share
    tracking. Mirrors run_nightly() holdings analysis but targets
    virtual_trades, not the real portfolio, and never writes to the shared
    data/plans/<ticker>.json store (save=False) so it can't clobber a real
    holding's plan for the same ticker.
    """
    today = str(date.today())
    open_tickers = get_open_tickers(db_path=db_path)
    print(f"[VT] Nightly exits: {len(open_tickers)} ticker(s) with open positions")

    for ticker in open_tickers:
        try:
            position = get_open_position(ticker, db_path=db_path)
            if not position:
                continue

            df = fetch_ohlcv(ticker)
            df = add_all_indicators(df)
            div = detect_rsi_divergence(df)
            tech_sig = get_technical_signal(ticker)
            fund = check_fundamentals(ticker)
            gate = earnings_gate(ticker)
            rs_data = {}
            try:
                rs_data = get_relative_strength(ticker)
            except Exception:
                pass

            clean = df.dropna(subset=["Close"])
            row = clean.iloc[-1]
            current_price = float(row["Close"])
            entry_price = position["entry_price"]
            pnl_pct = (current_price - entry_price) / entry_price

            try:
                shares = json.loads(position["entry_metrics"]).get("suggested_shares") or 1
            except (TypeError, ValueError, json.JSONDecodeError):
                shares = 1

            # Synthetic holding: entry price as cost basis, no real portfolio
            # weight to report since this is a standalone paper position.
            holding = {
                "ticker": ticker,
                "shares": shares,
                "avg_cost": entry_price,
                "current_price": current_price,
                "unrealized_pnl_pct": pnl_pct,
                "portfolio_pct": 0,
            }

            combined = generate_plan_with_news(holding, tech_sig, fund, gate, market, save=False)
            news = combined["news"]
            plan = combined["plan"]

            tier = _final_tier(
                tech_sig["tier"], fund["health"],
                news.get("sentiment", "neutral"), gate["proceed"], geo_risk, pnl_pct,
            )

            action = plan.get("action", "Hold")
            if action not in ("Start Trimming", "Exit"):
                print(f"[VT]   {ticker}: plan={action} (tier={tier}) — holding")
                continue

            exit_atr = float(row["atr"]) if pd.notna(row.get("atr")) else 1.0
            sz = size_swing_trade(
                ticker, current_price, exit_atr,
                tier=tier, portfolio_value=0, current_position_value=0,
            )
            metrics_json = build_metric_snapshot(
                ticker, df, div, tech_sig, fund, gate, news, rs_data, market, tier, sz, plan=plan
            )
            count = close_open_positions_for_ticker(
                ticker, today, current_price, action, metrics_json, db_path=db_path
            )
            print(f"[VT]   Closed {count} position(s) for {ticker} @ ${current_price:.2f} (plan={action}, tier={tier})")
        except Exception as e:
            print(f"[VT]   {ticker} exit failed: {e}")
