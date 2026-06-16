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


import pandas as pd
from src.virtual_trader import build_metric_snapshot


def _make_indicator_df() -> pd.DataFrame:
    """Minimal DataFrame with all columns that build_metric_snapshot reads."""
    return pd.DataFrame({
        "Close":        [98.0, 99.0, 100.0],
        "rsi":          [43.0, 44.0, 45.0],
        "bb_pct":       [0.18, 0.19, 0.20],
        "bb_width":     [0.04, 0.05, 0.06],
        "bb_upper":     [105.0, 106.0, 107.0],
        "bb_mid":       [100.0, 101.0, 102.0],
        "bb_lower":     [95.0,  96.0,  97.0],
        "macd":         [0.3,  0.4,  0.5],
        "macd_signal":  [0.2,  0.3,  0.4],
        "macd_hist":    [0.1,  0.1,  0.1],
        "ma50":         [97.0, 98.0, 99.0],
        "ma200":        [94.0, 95.0, 96.0],
        "atr":          [2.0,  2.1,  2.2],
        "volume_ratio": [1.1,  1.2,  1.3],
    })


class TestBuildMetricSnapshot:
    def _inputs(self):
        df = _make_indicator_df()
        div = {"bullish": True, "bearish": False, "bull_reason": "lower low, higher RSI", "bear_reason": ""}
        tech_sig = {
            "ticker": "AAPL", "tier": "Buy", "direction": "buy",
            "buy_score": 0.87, "sell_score": 0.25,
            "reasons": ["RSI: oversold (45.0)"], "misses": ["BB Width: bands expanding"],
            "price": 100.0, "rsi": 45.0, "atr": 2.2,
        }
        fund = {
            "health": "healthy", "revenue_growth": 0.12, "de_ratio": 45.0,
            "profit_margins": 0.18, "passed": ["Revenue growing (+12.0%)"], "flags": [],
        }
        gate = {"proceed": True, "reason": "No earnings within 14 days", "verdict": {}}
        news = {
            "sentiment": "positive", "health": "strong",
            "key_events": ["Product launch"], "red_flags": [],
            "confidence": "high", "verdict": "Strong fundamentals",
        }
        rs_data = {"relative_strength": 4.5, "label": "outperforming"}
        market = {
            "vix": {"level": 17.5, "sentiment": "neutral"},
            "fear_greed": {"value": 58, "label": "Greed"},
            "geopolitical": {"risk_level": "low"},
        }
        sz = {"shares": 6, "amount": 600.0}
        return df, div, tech_sig, fund, gate, news, rs_data, market, sz

    def test_returns_valid_json_string(self):
        df, div, tech_sig, fund, gate, news, rs_data, market, sz = self._inputs()
        result = build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, rs_data, market, "Buy", sz)
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_captures_last_row_indicator_values(self):
        df, div, tech_sig, fund, gate, news, rs_data, market, sz = self._inputs()
        data = json.loads(build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, rs_data, market, "Buy", sz))
        assert data["price"] == 100.0
        assert data["rsi"] == 45.0
        assert data["bb_pct"] == pytest.approx(0.20, abs=1e-5)
        assert data["ma50"] == pytest.approx(99.0, abs=1e-5)
        assert data["ma200"] == pytest.approx(96.0, abs=1e-5)
        assert data["volume_ratio"] == pytest.approx(1.3, abs=1e-5)

    def test_captures_technical_signal_fields(self):
        df, div, tech_sig, fund, gate, news, rs_data, market, sz = self._inputs()
        data = json.loads(build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, rs_data, market, "Buy", sz))
        assert data["buy_score"] == 0.87
        assert data["sell_score"] == 0.25
        assert data["technical_tier"] == "Buy"
        assert data["direction"] == "buy"
        assert "RSI: oversold (45.0)" in data["reasons"]

    def test_captures_divergence_flags(self):
        df, div, tech_sig, fund, gate, news, rs_data, market, sz = self._inputs()
        data = json.loads(build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, rs_data, market, "Buy", sz))
        assert data["rsi_div_bullish"] is True
        assert data["rsi_div_bearish"] is False
        assert data["rsi_div_bull_reason"] == "lower low, higher RSI"

    def test_captures_market_context(self):
        df, div, tech_sig, fund, gate, news, rs_data, market, sz = self._inputs()
        data = json.loads(build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, rs_data, market, "Buy", sz))
        assert data["vix_level"] == 17.5
        assert data["vix_sentiment"] == "neutral"
        assert data["fear_greed_value"] == 58
        assert data["geo_risk"] == "low"
        assert data["relative_strength"] == pytest.approx(4.5, abs=1e-5)
        assert data["rs_label"] == "outperforming"

    def test_captures_fundamentals(self):
        df, div, tech_sig, fund, gate, news, rs_data, market, sz = self._inputs()
        data = json.loads(build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, rs_data, market, "Buy", sz))
        assert data["fund_health"] == "healthy"
        assert data["fund_revenue_growth"] == pytest.approx(0.12, abs=1e-5)
        assert data["fund_de_ratio"] == pytest.approx(45.0, abs=1e-5)
        assert "Revenue growing (+12.0%)" in data["fund_passed"]
        assert data["fund_flags"] == []

    def test_captures_earnings_gate(self):
        df, div, tech_sig, fund, gate, news, rs_data, market, sz = self._inputs()
        data = json.loads(build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, rs_data, market, "Buy", sz))
        assert data["earnings_proceed"] is True
        assert data["earnings_reason"] == "No earnings within 14 days"

    def test_captures_news_fields(self):
        df, div, tech_sig, fund, gate, news, rs_data, market, sz = self._inputs()
        data = json.loads(build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, rs_data, market, "Buy", sz))
        assert data["news_sentiment"] == "positive"
        assert data["news_confidence"] == "high"
        assert "Product launch" in data["news_key_events"]

    def test_captures_final_tier_and_sizing(self):
        df, div, tech_sig, fund, gate, news, rs_data, market, sz = self._inputs()
        data = json.loads(build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, rs_data, market, "Strong Buy", sz))
        assert data["final_tier"] == "Strong Buy"
        assert data["suggested_shares"] == 6
        assert data["suggested_dollars"] == 600.0

    def test_handles_none_rs_data_gracefully(self):
        df, div, tech_sig, fund, gate, news, _, market, sz = self._inputs()
        data = json.loads(build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, {}, market, "Buy", sz))
        assert data["relative_strength"] is None
        assert data["rs_label"] is None

    def test_nan_indicator_values_become_null_in_json(self):
        """_f() must convert NaN to None — otherwise json.dumps raises on bad upstream data."""
        df, div, tech_sig, fund, gate, news, rs_data, market, sz = self._inputs()
        # Inject NaN into a column that goes through _f()
        df = df.copy()
        df.loc[df.index[-1], "rsi"] = float("nan")
        data = json.loads(build_metric_snapshot("AAPL", df, div, tech_sig, fund, gate, news, rs_data, market, "Buy", sz))
        assert data["rsi"] is None
