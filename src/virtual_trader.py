import json
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

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
) -> str:
    """Assemble a complete metric snapshot from all analysis components. Returns JSON string."""

    def _f(val):
        try:
            return round(float(val), 6) if pd.notna(val) else None
        except (TypeError, ValueError):
            return None

    row = df.iloc[-1]

    snapshot = {
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
        "buy_score":      tech_sig["buy_score"],
        "sell_score":     tech_sig["sell_score"],
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
        "vix_level":        market.get("vix", {}).get("level"),
        "vix_sentiment":    market.get("vix", {}).get("sentiment"),
        "fear_greed_value": market.get("fear_greed", {}).get("value"),
        "fear_greed_label": market.get("fear_greed", {}).get("label"),
        "geo_risk":         market.get("geopolitical", {}).get("risk_level", "low"),
        "relative_strength": rs_data.get("relative_strength") if rs_data else None,
        "rs_label":          rs_data.get("label") if rs_data else None,
        # Fundamentals
        "fund_health":         fund["health"],
        "fund_revenue_growth": fund.get("revenue_growth"),
        "fund_de_ratio":       fund.get("de_ratio"),
        "fund_profit_margins": fund.get("profit_margins"),
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
    }
    return json.dumps(snapshot)
