# Virtual Trader — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a silent, autonomous virtual trader that logs every buy/sell signal the existing system produces as real SQLite trades, capturing complete metric snapshots at entry and exit to build an ML training dataset.

**Architecture:** A new module `src/virtual_trader.py` owns all DB access and orchestration logic. `scheduler.py` calls two new functions (`run_virtual_entries`, `run_virtual_exits`) as isolated final steps in `run_weekly()` and `run_nightly()`. A prerequisite refactor extracts `_final_tier` from `scheduler.py` into `src/tier.py` so both modules can share it without circular imports.

**Tech Stack:** Python 3.11+, sqlite3 (stdlib), pytest, unittest.mock — no new dependencies.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/tier.py` | **Create** | Pure `_final_tier()` function, shared by scheduler and virtual trader |
| `src/virtual_trader.py` | **Create** | DB init, CRUD (open/close/query), `build_metric_snapshot`, `run_virtual_entries`, `run_virtual_exits` |
| `scheduler.py` | **Modify** | Import `_final_tier` from `src.tier`; add `run_virtual_entries` + `run_virtual_exits` calls |
| `tests/__init__.py` | **Create** | Empty file so pytest discovers the package |
| `tests/test_tier.py` | **Create** | Tests for `_final_tier` logic |
| `tests/test_virtual_trader.py` | **Create** | Tests for all DB functions, snapshot builder, entry/exit orchestration |

---

## Task 1: Extract `_final_tier` into `src/tier.py`

This breaks the would-be circular import: `virtual_trader.py` needs `_final_tier` but can't import from `scheduler.py` (which imports from `src/`).

**Files:**
- Create: `src/tier.py`
- Create: `tests/__init__.py`
- Create: `tests/test_tier.py`
- Modify: `scheduler.py` lines 35-59

- [ ] **Step 1: Write failing tests for `_final_tier`**

Create `tests/__init__.py` (empty):
```python
```

Create `tests/test_tier.py`:
```python
from src.tier import _final_tier


def test_healthy_positive_upgrades_buy_to_strong_buy():
    assert _final_tier("Buy", "healthy", "positive", True) == "Strong Buy"


def test_healthy_positive_keeps_strong_buy():
    assert _final_tier("Strong Buy", "healthy", "positive", True) == "Strong Buy"


def test_deteriorating_fundamentals_avoids_buy():
    assert _final_tier("Buy", "deteriorating", "positive", True) == "Avoid"


def test_negative_news_avoids_buy():
    assert _final_tier("Buy", "healthy", "negative", True) == "Avoid"


def test_gate_blocked_avoids_buy():
    assert _final_tier("Buy", "healthy", "positive", False) == "Avoid"


def test_hold_unchanged_when_neutral():
    assert _final_tier("Hold", "healthy", "positive", True) == "Hold"


def test_high_geo_risk_dampens_strong_buy_to_buy():
    assert _final_tier("Strong Buy", "healthy", "positive", True, geo_risk="high") == "Buy"


def test_severe_geo_kills_buy_to_hold():
    assert _final_tier("Buy", "healthy", "positive", True, geo_risk="severe") == "Hold"


def test_major_loser_blocks_sell():
    assert _final_tier("Sell", "healthy", "neutral", True, pnl_pct=-0.20) == "Hold"


def test_major_loser_does_not_block_sell_on_small_loss():
    # -14% is not a major loser (threshold is -15%)
    result = _final_tier("Sell", "healthy", "neutral", True, pnl_pct=-0.14)
    assert result == "Sell"


def test_severe_geo_and_profit_upgrades_sell_to_strong_sell():
    result = _final_tier("Sell", "healthy", "neutral", True, geo_risk="severe", pnl_pct=0.10)
    assert result == "Strong Sell"
```

- [ ] **Step 2: Run tests — verify they fail**

```
pytest tests/test_tier.py -v
```
Expected: `ModuleNotFoundError: No module named 'src.tier'`

- [ ] **Step 3: Create `src/tier.py` with `_final_tier`**

Create `src/tier.py` — copy the function verbatim from `scheduler.py` lines 35-59:
```python
def _final_tier(tech_tier, fund_health, news_sentiment, gate_proceed, geo_risk="low", pnl_pct=None):
    original_tier = tech_tier

    # Never cut losses on major drawdowns — hold through positions down >15%
    major_loser = pnl_pct is not None and pnl_pct < -0.15
    if major_loser and tech_tier in ("Sell", "Strong Sell"):
        tech_tier = "Hold"

    if fund_health == "deteriorating" or news_sentiment == "negative" or not gate_proceed:
        return "Avoid" if tech_tier in ("Strong Buy", "Buy") else tech_tier

    # Geo dampens buy-side conviction — severe kills the signal entirely
    if geo_risk in ("high", "severe") and tech_tier == "Strong Buy":
        tech_tier = "Buy"
    if geo_risk == "severe" and tech_tier == "Buy":
        tech_tier = "Hold"

    # Geo amplifies an existing sell signal when in profit — never invents one from Hold
    in_profit = pnl_pct is not None and pnl_pct > 0
    if in_profit and geo_risk == "severe" and original_tier == "Sell":
        tech_tier = "Strong Sell"

    if fund_health == "healthy" and news_sentiment == "positive":
        return "Strong Buy" if tech_tier == "Buy" else ("Strong Sell" if tech_tier == "Sell" else tech_tier)
    return tech_tier
```

- [ ] **Step 4: Update `scheduler.py` to import from `src.tier`**

In `scheduler.py`, replace lines 35-59 (the `_final_tier` definition) with an import:
```python
from src.tier import _final_tier
```
Add this import after the existing `from src.market_context import get_relative_strength` line at the top.

- [ ] **Step 5: Run tests — verify they pass**

```
pytest tests/test_tier.py -v
```
Expected: 11 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/tier.py tests/__init__.py tests/test_tier.py scheduler.py
git commit -m "refactor: extract _final_tier into src/tier.py for shared use"
```

---

## Task 2: Create `src/virtual_trader.py` — DB schema and CRUD

**Files:**
- Create: `src/virtual_trader.py`
- Create: `tests/test_virtual_trader.py`

- [ ] **Step 1: Write failing tests for DB functions**

Create `tests/test_virtual_trader.py`:
```python
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


# ── Import the functions we're about to build ────────────────────────────────
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
            with sqlite3.connect(str(tmp)) as conn:
                cur = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='virtual_trades'"
                )
                assert cur.fetchone() is not None
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
            with sqlite3.connect(str(tmp)) as conn:
                row = conn.execute("SELECT ticker, status FROM virtual_trades WHERE id=1").fetchone()
            assert row == ("AAPL", "open")
        finally:
            os.unlink(tmp)

    def test_allows_multiple_positions_same_ticker(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("AAPL", "2026-06-01", 145.00, "Buy", '{}', db_path=tmp)
            open_position("AAPL", "2026-06-08", 148.00, "Buy", '{}', db_path=tmp)
            with sqlite3.connect(str(tmp)) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM virtual_trades WHERE ticker='AAPL'"
                ).fetchone()[0]
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
            with sqlite3.connect(str(tmp)) as conn:
                row = conn.execute("SELECT return_pct, holding_days FROM virtual_trades WHERE id=1").fetchone()
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
```

- [ ] **Step 2: Run tests — verify they fail**

```
pytest tests/test_virtual_trader.py -v
```
Expected: `ModuleNotFoundError: No module named 'src.virtual_trader'`

- [ ] **Step 3: Implement DB functions in `src/virtual_trader.py`**

Create `src/virtual_trader.py`:
```python
import json
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "virtual_trader.db"


def _init_db(db_path: Path = None) -> None:
    path = str(db_path or DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
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
    with sqlite3.connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO virtual_trades
               (ticker, status, entry_date, entry_price, entry_tier, entry_metrics)
               VALUES (?, 'open', ?, ?, ?, ?)""",
            (ticker, entry_date, entry_price, entry_tier, entry_metrics),
        )
        conn.commit()
        return cur.lastrowid


def close_open_positions_for_ticker(
    ticker: str,
    exit_date: str,
    exit_price: float,
    exit_tier: str,
    exit_metrics: str,
    db_path: Path = None,
) -> int:
    path = str(db_path or DB_PATH)
    with sqlite3.connect(path) as conn:
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


def get_open_tickers(db_path: Path = None) -> list[str]:
    path = str(db_path or DB_PATH)
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM virtual_trades WHERE status='open'"
        ).fetchall()
        return [r[0] for r in rows]
```

- [ ] **Step 4: Run tests — verify they pass**

```
pytest tests/test_virtual_trader.py -v
```
Expected: all 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/virtual_trader.py tests/test_virtual_trader.py
git commit -m "feat: add virtual_trader DB schema and CRUD functions"
```

---

## Task 3: Add `build_metric_snapshot()` to `src/virtual_trader.py`

**Files:**
- Modify: `src/virtual_trader.py`
- Modify: `tests/test_virtual_trader.py`

- [ ] **Step 1: Write failing test for `build_metric_snapshot`**

Append to `tests/test_virtual_trader.py`:
```python
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
```

- [ ] **Step 2: Run tests — verify new tests fail**

```
pytest tests/test_virtual_trader.py::TestBuildMetricSnapshot -v
```
Expected: `ImportError: cannot import name 'build_metric_snapshot'`

- [ ] **Step 3: Implement `build_metric_snapshot` in `src/virtual_trader.py`**

Append to `src/virtual_trader.py` (after the existing CRUD functions):
```python
import pandas as pd


def build_metric_snapshot(
    ticker: str,
    df: pd.DataFrame,
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
        "rsi_div_bullish":    div["bullish"],
        "rsi_div_bearish":    div["bearish"],
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
        "fund_health":          fund["health"],
        "fund_revenue_growth":  fund.get("revenue_growth"),
        "fund_de_ratio":        fund.get("de_ratio"),
        "fund_profit_margins":  fund.get("profit_margins"),
        "fund_passed":          fund.get("passed", []),
        "fund_flags":           fund.get("flags", []),
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
        "final_tier":       final_tier,
        "suggested_shares": sz.get("shares"),
        "suggested_dollars": sz.get("amount"),
    }
    return json.dumps(snapshot)
```

Also add `import pandas as pd` at the top of `src/virtual_trader.py` (after `import json`).

- [ ] **Step 4: Run tests — verify they pass**

```
pytest tests/test_virtual_trader.py -v
```
Expected: all tests (DB + snapshot) PASS

- [ ] **Step 5: Commit**

```bash
git add src/virtual_trader.py tests/test_virtual_trader.py
git commit -m "feat: add build_metric_snapshot to virtual_trader"
```

---

## Task 4: Add `run_virtual_entries()` and wire into `run_weekly()`

**Files:**
- Modify: `src/virtual_trader.py`
- Modify: `scheduler.py`
- Modify: `tests/test_virtual_trader.py`

- [ ] **Step 1: Write failing tests for `run_virtual_entries`**

Append to `tests/test_virtual_trader.py`:
```python
from unittest.mock import patch, MagicMock
from src.virtual_trader import run_virtual_entries


def _fake_analysis_mocks(final_tier_return: str):
    """Returns a dict of patches for all external calls in run_virtual_entries."""
    fake_df = _make_indicator_df()
    return {
        "src.virtual_trader.fetch_ohlcv": MagicMock(return_value=fake_df),
        "src.virtual_trader.add_all_indicators": MagicMock(return_value=fake_df),
        "src.virtual_trader.detect_rsi_divergence": MagicMock(return_value={
            "bullish": False, "bearish": False, "bull_reason": "", "bear_reason": "",
        }),
        "src.virtual_trader.get_technical_signal": MagicMock(return_value={
            "ticker": "AAPL", "tier": "Strong Buy", "direction": "buy",
            "buy_score": 1.0, "sell_score": 0.1, "reasons": ["all conditions met"], "misses": [],
            "price": 150.0, "rsi": 42.0, "atr": 2.5,
        }),
        "src.virtual_trader.check_fundamentals": MagicMock(return_value={
            "health": "healthy", "revenue_growth": 0.15, "de_ratio": 40.0,
            "profit_margins": 0.20, "passed": ["Revenue growing"], "flags": [],
        }),
        "src.virtual_trader.earnings_gate": MagicMock(return_value={
            "proceed": True, "reason": "No earnings", "verdict": {},
        }),
        "src.virtual_trader.analyze_company": MagicMock(return_value={
            "sentiment": "positive", "health": "strong", "key_events": [],
            "red_flags": [], "confidence": "high", "verdict": "Buy",
        }),
        "src.virtual_trader.get_relative_strength": MagicMock(return_value={
            "relative_strength": 6.0, "label": "outperforming",
        }),
        "src.virtual_trader.size_swing_trade": MagicMock(return_value={
            "shares": 5, "amount": 750.0,
        }),
        "src.virtual_trader._final_tier": MagicMock(return_value=final_tier_return),
    }


class TestRunVirtualEntries:
    def _cache(self, tier="Strong Buy"):
        return {"results": [{"ticker": "AAPL", "tier": tier, "price": 150.0, "atr": 2.5}]}

    def _market(self):
        return {
            "vix": {"level": 17.0, "sentiment": "neutral"},
            "fear_greed": {"value": 60, "label": "Greed"},
            "geopolitical": {"risk_level": "low"},
        }

    def test_opens_position_when_final_tier_is_strong_buy(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            with patch.multiple("src.virtual_trader", **_fake_analysis_mocks("Strong Buy")):
                run_virtual_entries(self._cache(), self._market(), "low", db_path=tmp)
            assert get_open_tickers(db_path=tmp) == ["AAPL"]
        finally:
            os.unlink(tmp)

    def test_opens_position_when_final_tier_is_buy(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            with patch.multiple("src.virtual_trader", **_fake_analysis_mocks("Buy")):
                run_virtual_entries(self._cache(), self._market(), "low", db_path=tmp)
            assert get_open_tickers(db_path=tmp) == ["AAPL"]
        finally:
            os.unlink(tmp)

    def test_skips_when_final_tier_is_avoid(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            with patch.multiple("src.virtual_trader", **_fake_analysis_mocks("Avoid")):
                run_virtual_entries(self._cache(), self._market(), "low", db_path=tmp)
            assert get_open_tickers(db_path=tmp) == []
        finally:
            os.unlink(tmp)

    def test_skips_tickers_not_in_buy_tiers(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            cache = {"results": [{"ticker": "AAPL", "tier": "Hold", "price": 150.0, "atr": 2.5}]}
            with patch.multiple("src.virtual_trader", **_fake_analysis_mocks("Hold")):
                run_virtual_entries(cache, self._market(), "low", db_path=tmp)
            assert get_open_tickers(db_path=tmp) == []
        finally:
            os.unlink(tmp)

    def test_continues_after_per_ticker_exception(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            cache = {"results": [
                {"ticker": "FAIL", "tier": "Strong Buy", "price": 100.0, "atr": 2.0},
                {"ticker": "AAPL", "tier": "Strong Buy", "price": 150.0, "atr": 2.5},
            ]}
            mocks = _fake_analysis_mocks("Strong Buy")
            # Make the first call to get_technical_signal raise, second succeed
            mocks["src.virtual_trader.get_technical_signal"] = MagicMock(
                side_effect=[RuntimeError("network error"), {
                    "ticker": "AAPL", "tier": "Strong Buy", "direction": "buy",
                    "buy_score": 1.0, "sell_score": 0.1, "reasons": [], "misses": [],
                    "price": 150.0, "rsi": 42.0, "atr": 2.5,
                }]
            )
            with patch.multiple("src.virtual_trader", **mocks):
                run_virtual_entries(cache, self._market(), "low", db_path=tmp)
            assert get_open_tickers(db_path=tmp) == ["AAPL"]
        finally:
            os.unlink(tmp)
```

- [ ] **Step 2: Run tests — verify they fail**

```
pytest tests/test_virtual_trader.py::TestRunVirtualEntries -v
```
Expected: `ImportError: cannot import name 'run_virtual_entries'`

- [ ] **Step 3: Implement `run_virtual_entries` in `src/virtual_trader.py`**

Add these imports to the top of `src/virtual_trader.py` (after existing imports):
```python
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
```

Append `run_virtual_entries` to `src/virtual_trader.py`:
```python
def run_virtual_entries(cache: dict, market: dict, geo_risk: str, db_path: Path = None) -> None:
    """
    Weekly: open a virtual position for every ticker where the full two-pillar
    analysis produces Strong Buy or Buy. Mirrors run_weekly() but covers the
    entire screener universe (not just the top 5) and writes to virtual_trades.
    """
    today = str(date.today())
    candidates = [r for r in cache.get("results", []) if r.get("tier") in ("Strong Buy", "Buy")]
    print(f"[VT] Weekly entries: {len(candidates)} technical buy candidates")

    for candidate in candidates:
        ticker = candidate["ticker"]
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

            tier = _final_tier(
                tech_sig["tier"], fund["health"],
                news.get("sentiment", "neutral"), gate["proceed"], geo_risk,
            )
            if tier not in ("Strong Buy", "Buy"):
                continue

            sz = size_swing_trade(
                ticker, tech_sig["price"], tech_sig.get("atr") or 1,
                tier=tier, portfolio_value=0, current_position_value=0,
            )
            metrics_json = build_metric_snapshot(
                ticker, df, div, tech_sig, fund, gate, news, rs_data, market, tier, sz
            )
            open_position(ticker, today, tech_sig["price"], tier, metrics_json, db_path=db_path)
            print(f"[VT]   Opened {ticker} @ ${tech_sig['price']:.2f} ({tier})")
        except Exception as e:
            print(f"[VT]   {ticker} entry failed: {e}")
```

- [ ] **Step 4: Wire `run_virtual_entries` into `run_weekly()` in `scheduler.py`**

In `scheduler.py`, add this import near the top alongside the other `src` imports:
```python
from src.virtual_trader import run_virtual_entries
```

(Task 5 will extend this import to also include `run_virtual_exits`.)

At the end of `run_weekly()` (before the `elapsed` line), add:
```python
    # ── Virtual trader entries (isolated — does not affect user-facing output) ──
    print("\n[ VT ] Running virtual trader entries...")
    try:
        run_virtual_entries(cache, market, geo_risk)
    except Exception as e:
        print(f"  Virtual trader entries failed: {e}")
```

- [ ] **Step 5: Run tests — verify they pass**

```
pytest tests/test_virtual_trader.py -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/virtual_trader.py scheduler.py tests/test_virtual_trader.py
git commit -m "feat: add run_virtual_entries and wire into run_weekly"
```

---

## Task 5: Add `run_virtual_exits()` and wire into `run_nightly()`

**Files:**
- Modify: `src/virtual_trader.py`
- Modify: `scheduler.py`
- Modify: `tests/test_virtual_trader.py`

- [ ] **Step 1: Write failing tests for `run_virtual_exits`**

Append to `tests/test_virtual_trader.py`:
```python
from src.virtual_trader import run_virtual_exits


def _fake_exit_mocks(final_tier_return: str, price: float = 160.0):
    fake_df = _make_indicator_df()
    return {
        "src.virtual_trader.fetch_ohlcv": MagicMock(return_value=fake_df),
        "src.virtual_trader.add_all_indicators": MagicMock(return_value=fake_df),
        "src.virtual_trader.detect_rsi_divergence": MagicMock(return_value={
            "bullish": False, "bearish": True, "bull_reason": "", "bear_reason": "higher high, lower RSI",
        }),
        "src.virtual_trader.get_technical_signal": MagicMock(return_value={
            "ticker": "AAPL", "tier": "Sell", "direction": "sell",
            "buy_score": 0.12, "sell_score": 0.88, "reasons": ["BB: near upper band"], "misses": [],
            "price": price, "rsi": 76.0, "atr": 2.5,
        }),
        "src.virtual_trader.check_fundamentals": MagicMock(return_value={
            "health": "neutral", "revenue_growth": 0.05, "de_ratio": 60.0,
            "profit_margins": 0.10, "passed": [], "flags": [],
        }),
        "src.virtual_trader.earnings_gate": MagicMock(return_value={
            "proceed": True, "reason": "No earnings", "verdict": {},
        }),
        "src.virtual_trader.analyze_company": MagicMock(return_value={
            "sentiment": "neutral", "health": "ok", "key_events": [],
            "red_flags": [], "confidence": "medium", "verdict": "Hold",
        }),
        "src.virtual_trader.get_relative_strength": MagicMock(return_value={}),
        "src.virtual_trader.size_swing_trade": MagicMock(return_value={"shares": 0, "amount": 0.0}),
        "src.virtual_trader._final_tier": MagicMock(return_value=final_tier_return),
    }


class TestRunVirtualExits:
    def _market(self):
        return {
            "vix": {"level": 22.0, "sentiment": "neutral"},
            "fear_greed": {"value": 40, "label": "Fear"},
            "geopolitical": {"risk_level": "low"},
        }

    def test_closes_position_on_sell_signal(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("AAPL", "2026-06-01", 145.00, "Buy", '{}', db_path=tmp)
            with patch.multiple("src.virtual_trader", **_fake_exit_mocks("Sell")):
                run_virtual_exits(self._market(), "low", db_path=tmp)
            assert get_open_tickers(db_path=tmp) == []
        finally:
            os.unlink(tmp)

    def test_closes_position_on_strong_sell_signal(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("AAPL", "2026-06-01", 145.00, "Buy", '{}', db_path=tmp)
            with patch.multiple("src.virtual_trader", **_fake_exit_mocks("Strong Sell")):
                run_virtual_exits(self._market(), "low", db_path=tmp)
            assert get_open_tickers(db_path=tmp) == []
        finally:
            os.unlink(tmp)

    def test_holds_position_when_not_sell_tier(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("AAPL", "2026-06-01", 145.00, "Buy", '{}', db_path=tmp)
            with patch.multiple("src.virtual_trader", **_fake_exit_mocks("Hold")):
                run_virtual_exits(self._market(), "low", db_path=tmp)
            assert get_open_tickers(db_path=tmp) == ["AAPL"]
        finally:
            os.unlink(tmp)

    def test_closes_multiple_open_positions_for_same_ticker(self):
        import sqlite3
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("AAPL", "2026-06-01", 145.00, "Buy", '{}', db_path=tmp)
            open_position("AAPL", "2026-06-08", 148.00, "Buy", '{}', db_path=tmp)
            with patch.multiple("src.virtual_trader", **_fake_exit_mocks("Sell", price=160.0)):
                run_virtual_exits(self._market(), "low", db_path=tmp)
            assert get_open_tickers(db_path=tmp) == []
            with sqlite3.connect(str(tmp)) as conn:
                rows = conn.execute("SELECT return_pct FROM virtual_trades ORDER BY id").fetchall()
            assert abs(rows[0][0] - (160.0 - 145.0) / 145.0) < 1e-5
            assert abs(rows[1][0] - (160.0 - 148.0) / 148.0) < 1e-5
        finally:
            os.unlink(tmp)

    def test_does_not_check_tickers_with_no_open_positions(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            mocks = _fake_exit_mocks("Sell")
            with patch.multiple("src.virtual_trader", **mocks):
                run_virtual_exits(self._market(), "low", db_path=tmp)
            mocks["src.virtual_trader.get_technical_signal"].assert_not_called()
        finally:
            os.unlink(tmp)

    def test_continues_after_per_ticker_exception(self):
        tmp = _tmp_db()
        try:
            _init_db(tmp)
            open_position("FAIL", "2026-06-01", 50.00, "Buy", '{}', db_path=tmp)
            open_position("AAPL", "2026-06-01", 145.00, "Buy", '{}', db_path=tmp)
            mocks = _fake_exit_mocks("Sell")
            mocks["src.virtual_trader.get_technical_signal"] = MagicMock(
                side_effect=[RuntimeError("timeout"), {
                    "ticker": "AAPL", "tier": "Sell", "direction": "sell",
                    "buy_score": 0.1, "sell_score": 0.88, "reasons": [], "misses": [],
                    "price": 160.0, "rsi": 76.0, "atr": 2.5,
                }]
            )
            with patch.multiple("src.virtual_trader", **mocks):
                run_virtual_exits(self._market(), "low", db_path=tmp)
            assert "FAIL" in get_open_tickers(db_path=tmp)
            assert "AAPL" not in get_open_tickers(db_path=tmp)
        finally:
            os.unlink(tmp)
```

- [ ] **Step 2: Run tests — verify they fail**

```
pytest tests/test_virtual_trader.py::TestRunVirtualExits -v
```
Expected: `ImportError: cannot import name 'run_virtual_exits'`

- [ ] **Step 3: Implement `run_virtual_exits` in `src/virtual_trader.py`**

Append to `src/virtual_trader.py`:
```python
def run_virtual_exits(market: dict, geo_risk: str, db_path: Path = None) -> None:
    """
    Nightly: check every open virtual position. If the full two-pillar analysis
    produces Sell or Strong Sell, close all open positions for that ticker.
    Mirrors run_nightly() holdings analysis but targets virtual_trades, not the
    real portfolio.
    """
    today = str(date.today())
    open_tickers = get_open_tickers(db_path=db_path)
    print(f"[VT] Nightly exits: {len(open_tickers)} ticker(s) with open positions")

    for ticker in open_tickers:
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

            tier = _final_tier(
                tech_sig["tier"], fund["health"],
                news.get("sentiment", "neutral"), gate["proceed"], geo_risk,
            )
            if tier not in ("Sell", "Strong Sell"):
                print(f"[VT]   {ticker}: {tier} — holding")
                continue

            sz = size_swing_trade(
                ticker, tech_sig["price"], tech_sig.get("atr") or 1,
                tier=tier, portfolio_value=0, current_position_value=0,
            )
            metrics_json = build_metric_snapshot(
                ticker, df, div, tech_sig, fund, gate, news, rs_data, market, tier, sz
            )
            count = close_open_positions_for_ticker(
                ticker, today, tech_sig["price"], tier, metrics_json, db_path=db_path
            )
            print(f"[VT]   Closed {count} position(s) for {ticker} @ ${tech_sig['price']:.2f} ({tier})")
        except Exception as e:
            print(f"[VT]   {ticker} exit failed: {e}")
```

- [ ] **Step 4: Wire `run_virtual_exits` into `run_nightly()` in `scheduler.py`**

Update the import added in Task 4 at the top of `scheduler.py`:
```python
from src.virtual_trader import run_virtual_entries, run_virtual_exits
```

In `run_nightly()`, after the portfolio strategy block (after the `try/except` for strategy) and before the `elapsed` line, add:
```python
    # ── Virtual trader exits (isolated — does not affect user-facing output) ───
    print("\n[ VT ] Running virtual trader exits...")
    try:
        run_virtual_exits(market, geo_risk)
    except Exception as e:
        print(f"  Virtual trader exits failed: {e}")
```

- [ ] **Step 5: Run all tests — verify full suite passes**

```
pytest tests/ -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/virtual_trader.py scheduler.py tests/test_virtual_trader.py
git commit -m "feat: add run_virtual_exits and wire into run_nightly"
```

---

## Querying the Data

Once data accumulates, inspect it with:

```python
import sqlite3, pandas as pd, json

conn = sqlite3.connect("data/virtual_trader.db")

# All closed trades with returns
df = pd.read_sql("SELECT * FROM virtual_trades WHERE status='closed'", conn)
df["entry_metrics"] = df["entry_metrics"].apply(json.loads)
df["exit_metrics"]  = df["exit_metrics"].apply(json.loads)

# Quick summary
print(df[["ticker", "entry_date", "exit_date", "entry_tier", "return_pct", "holding_days"]].to_string())

conn.close()
```
