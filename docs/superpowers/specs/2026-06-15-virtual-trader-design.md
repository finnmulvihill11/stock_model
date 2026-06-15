# Virtual Trader — Phase 1 Data Collection (Design)

## Background

Long-term goal: replace the generic Claude-driven analysis (Pillar 2 fundamentals/news + plan generation) with a model fine-tuned specifically on this project's own buy/sell philosophy. Fine-tuning requires a labeled dataset of (decision conditions → outcome), which doesn't exist yet.

This is **Phase 1** of that initiative: a fully automated, silent data-collection system that logs every signal the existing rule-based + Claude system would act on, and tracks the actual outcome. Phase 2 (fine-tuning pipeline) and Phase 3 (swap-in to replace Claude calls) are separate, future specs — they depend on having months of data from this phase.

## Goal

Build a "virtual trader" that mirrors what the live system would do if every Strong Buy/Buy and Sell/Strong Sell signal were acted on automatically, across the full S&P 500 + NASDAQ 100 universe. Every open and close event records a complete snapshot of every metric that fed into the decision, so the resulting dataset can later be mined for (entry conditions → % return) relationships and used to train an ML model.

This is purely additive: it does not change any user-facing signals, plans, dashboards, or alerts. It runs as a separate, silent step within the existing scheduler.

## Data Model

New SQLite database: `data/virtual_trader.db`, single table `virtual_trades`:

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `ticker` | TEXT | e.g. "AAPL" |
| `status` | TEXT | `open` or `closed` |
| `entry_date` | TEXT (ISO date) | date the virtual position was opened |
| `entry_price` | REAL | Close price on entry date |
| `entry_tier` | TEXT | `Strong Buy` or `Buy` — the `final_tier` that triggered entry |
| `entry_metrics` | TEXT (JSON) | full metric snapshot at entry (see below) |
| `exit_date` | TEXT (ISO date), nullable | date the position was closed |
| `exit_price` | REAL, nullable | Close price on exit date |
| `exit_tier` | TEXT, nullable | `Sell` or `Strong Sell` — the `final_tier` that triggered exit |
| `exit_metrics` | TEXT (JSON), nullable | full metric snapshot at exit (same shape as entry_metrics) |
| `return_pct` | REAL, nullable | `(exit_price - entry_price) / entry_price` |
| `holding_days` | INTEGER, nullable | calendar days between entry_date and exit_date |

### Metric snapshot shape (entry_metrics / exit_metrics JSON)

Captured by pulling the indicator dataframe directly (via `fetch_ohlcv` + `add_all_indicators` + `detect_rsi_divergence`) rather than relying on any trimmed return dict, so nothing the live system considers is lost:

- **Price/technical**: `price`, `rsi`, `bb_pct`, `bb_width`, `bb_upper`, `bb_mid`, `bb_lower`, `macd`, `macd_signal`, `macd_hist`, `ma50`, `ma200`, `atr`, `volume_ratio`, `buy_score`, `sell_score`, `technical_tier`, divergence flags + reasons, passed/failed condition lists from `signals.py`
- **Market context**: VIX level, Fear & Greed, relative strength vs SPY, geo risk level
- **Fundamentals**: health label, revenue growth, debt-to-equity, passed/flagged items from `check_fundamentals`
- **Earnings gate**: proceed flag, earnings status, verdict from `earnings_gate`
- **News (Claude)**: sentiment, health, key events, red flags, confidence, verdict text from `analyze_company`
- **Final**: `final_tier`, suggested position size/conviction from `size_swing_trade`

A JSON blob is used (rather than individual columns) because the indicator/metric set will evolve over time — this avoids schema migrations while remaining queryable via SQLite's `json_extract` or by loading into pandas with `json_normalize`.

## Workflow

Mirrors the cadence of the real user-facing flow (weekly discovery, nightly holdings analysis), but as a fully separate code path — no shared state or output with the existing `position_plans`, `signals`, or digest/alerts.

### Weekly (Sunday, `run_weekly()`) — Entries only

After the existing `run_full_scrape()`:

1. For every ticker in the full universe where the technical (Pillar 1) tier is Strong Buy/Buy:
   - Run Pillar 2 (fundamentals + news via `check_fundamentals` / `analyze_company`) to compute `final_tier`, exactly as the existing opportunity-analysis path does.
   - If `final_tier` is Strong Buy/Buy, **open a new virtual position**: insert a row with `status="open"`, `entry_date`=today, `entry_price`=current Close, `entry_tier`=final_tier, `entry_metrics`=full snapshot.
2. Multiple open positions per ticker are allowed. If the signal persists week over week, a new row is opened each week regardless of existing open positions for that ticker.

### Nightly (Mon-Fri, `run_nightly()`) — Exits only

After the existing holdings analysis (separate step, does not modify it):

1. For every distinct ticker that has ≥1 row with `status="open"` in `virtual_trades`:
   - Compute `final_tier` (Pillar 1 + Pillar 2), same as the live holdings analysis path.
   - If `final_tier` is Sell/Strong Sell, **close all open positions for that ticker**: for each open row, set `status="closed"`, `exit_date`=today, `exit_price`=current Close, `exit_tier`=final_tier, `exit_metrics`=full snapshot, `return_pct`=computed, `holding_days`=computed.
2. If `final_tier` is not Sell/Strong Sell, leave open positions untouched.

### Isolation from user-facing flow

- New module `src/virtual_trader.py` owns all DB access (`open_position`, `close_open_positions_for_ticker`, `get_open_tickers`, snapshot-building helper).
- Called from `scheduler.py` as additional, isolated steps in `run_weekly()` and `run_nightly()`, wrapped in try/except per ticker (consistent with existing error handling) so failures don't affect the user-facing digest/plans/alerts.
- No changes to `app.py`, `alerts.py`, `planner.py`, or any existing analysis-cache files.

## Out of Scope (Phase 1)

- No Streamlit/frontend view — query `data/virtual_trader.db` directly (e.g. via sqlite3 or pandas) when inspecting data.
- No dollar-amount or cash-balance tracking — `return_pct` is the only outcome metric that matters.
- No ML training pipeline — that's Phase 2, once enough data has accumulated (likely months).
- No changes to real signals, plans, or alerts.

## Open Questions

None — design fully reviewed and approved.
