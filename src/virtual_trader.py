import json
import sqlite3
from datetime import date
from pathlib import Path

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
