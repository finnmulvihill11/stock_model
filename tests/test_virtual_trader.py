import json
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

import pytest


def _tmp_db() -> Path:
    """Return path to a fresh temp SQLite file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return Path(path)


from src.virtual_trader import (
    _init_db,
    open_position,
    close_open_positions_for_ticker,
    get_open_tickers,
)


class TestInitDb:
    def test_creates_virtual_trades_table(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            conn = sqlite3.connect(str(tmp))
            try:
                cur = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='virtual_trades'"
                )
                assert cur.fetchone() is not None
            finally:
                conn.close()
        finally:
            os.unlink(tmp)

    def test_idempotent(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            _init_db(tmp)  # second call must not raise
        finally:
            os.unlink(tmp)


class TestOpenPosition:
    def test_inserts_row_with_status_open(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            row_id = open_position("AAPL", "2026-06-15", 150.00, "Strong Buy", '{"price":150}', db_path=tmp)
            assert row_id == 1
            conn = sqlite3.connect(str(tmp))
            try:
                row = conn.execute("SELECT ticker, status FROM virtual_trades WHERE id=1").fetchone()
            finally:
                conn.close()
            assert row == ("AAPL", "open")
        finally:
            os.unlink(tmp)

    def test_allows_multiple_positions_same_ticker(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("AAPL", "2026-06-01", 145.00, "Buy", '{}', db_path=tmp)
            open_position("AAPL", "2026-06-08", 148.00, "Buy", '{}', db_path=tmp)
            conn = sqlite3.connect(str(tmp))
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM virtual_trades WHERE ticker='AAPL'"
                ).fetchone()[0]
            finally:
                conn.close()
            assert count == 2
        finally:
            os.unlink(tmp)


class TestGetOpenTickers:
    def test_returns_distinct_tickers(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("AAPL", "2026-06-01", 145.00, "Buy", '{}', db_path=tmp)
            open_position("AAPL", "2026-06-08", 148.00, "Buy", '{}', db_path=tmp)
            open_position("MSFT", "2026-06-01", 400.00, "Strong Buy", '{}', db_path=tmp)
            tickers = get_open_tickers(db_path=tmp)
            assert sorted(tickers) == ["AAPL", "MSFT"]
        finally:
            os.unlink(tmp)

    def test_empty_when_no_open_positions(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            assert get_open_tickers(db_path=tmp) == []
        finally:
            os.unlink(tmp)


class TestCloseOpenPositions:
    def test_closes_all_open_positions_for_ticker(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("AAPL", "2026-06-01", 145.00, "Buy", '{}', db_path=tmp)
            open_position("AAPL", "2026-06-08", 148.00, "Buy", '{}', db_path=tmp)
            count = close_open_positions_for_ticker("AAPL", "2026-06-15", 160.00, "Sell", '{}', db_path=tmp)
            assert count == 2
            assert get_open_tickers(db_path=tmp) == []
        finally:
            os.unlink(tmp)

    def test_computes_return_pct_correctly(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("AAPL", "2026-06-01", 100.00, "Buy", '{}', db_path=tmp)
            close_open_positions_for_ticker("AAPL", "2026-06-15", 110.00, "Sell", '{}', db_path=tmp)
            conn = sqlite3.connect(str(tmp))
            try:
                row = conn.execute("SELECT return_pct, holding_days FROM virtual_trades WHERE id=1").fetchone()
            finally:
                conn.close()
            assert abs(row[0] - 0.10) < 1e-6
            assert row[1] == 14
        finally:
            os.unlink(tmp)

    def test_does_not_affect_other_tickers(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("AAPL", "2026-06-01", 150.00, "Buy", '{}', db_path=tmp)
            open_position("MSFT", "2026-06-01", 400.00, "Buy", '{}', db_path=tmp)
            close_open_positions_for_ticker("AAPL", "2026-06-15", 160.00, "Sell", '{}', db_path=tmp)
            assert get_open_tickers(db_path=tmp) == ["MSFT"]
        finally:
            os.unlink(tmp)

    def test_returns_zero_when_no_open_positions(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            count = close_open_positions_for_ticker("AAPL", "2026-06-15", 160.00, "Sell", '{}', db_path=tmp)
            assert count == 0
        finally:
            os.unlink(tmp)
