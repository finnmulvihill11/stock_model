# Project Design Record — Personal Stock Analysis Tool

## Problem Statement

Managing a mid/long-term equity portfolio requires regular chart review to catch buy and sell opportunities. As a busy MIT student, manual chart-checking is inconsistent, leading to missed entries and exits. This tool automates that monitoring loop so the investor gets actionable signals without having to watch charts.

---

## Goals

| Priority | Goal |
|----------|------|
| P0 | Encode buy/sell signal system as executable logic |
| P0 | News + earnings analysis via Claude API |
| P0 | Streamlit frontend dashboard |
| P1 | Position sizing recommendations with suggested amounts |
| P1 | Stock screener — surface new candidates + average-down opportunities |
| P1 | E*Trade API integration for live portfolio data |
| P1 | Per-company investment plan tracker |
| P1 | Overall investment strategy dashboard |
| P2 | DCA scheduler for ETF positions |
| P2 | Daily digest via GitHub Actions + email |

---

## Non-Goals

- Intraday / day trading signals
- Options or leveraged instruments
- Fully automated order execution (decision-support only)
- Manual plan entry (tool generates all plans autonomously)

---

## Trading Style

- **Horizon**: weeks to a few months (swing to mid-term)
- **Style**: mean-reversion swing trading on quality companies; reputable names, no penny stocks
- **ETF anchor**: VOO + VXUS as DCA baseline, held regardless of signals
- **Risk**: growth-oriented, moderate — not reckless

---

## Signal Architecture — Two Pillars

Every signal is the product of two independent pillars evaluated in parallel. Neither alone is sufficient.

```
TECHNICAL PILLAR          FUNDAMENTAL/NEWS PILLAR
(when to act)             (whether the company deserves the trade)
       |                              |
       +-----------> FINAL SIGNAL <--+
```

### Final Signal Tiers

| Signal | Technical Pillar | Fundamental/News Pillar |
|--------|-----------------|------------------------|
| **Strong Buy** | All conditions aligned | Healthy fundamentals + positive news/macro |
| **Buy** | All conditions aligned | Healthy fundamentals + neutral news |
| **Watch** | Partially aligned | Mixed picture — monitor closely |
| **Avoid** | Looks like a buy technically | Fundamentals weak OR news/macro negative |
| **Sell** | Sell conditions aligned | Any pillar deteriorating |
| **Strong Sell** | Sell conditions aligned | Fundamentals deteriorating + negative news |

The **Avoid** tier is critical — it catches setups that look good on a chart but where the holistic picture says don't. This is how you avoid buying a technically oversold stock that's oversold for a real reason.

---

## Pillar 1 — Technical Signals

### Buy Conditions (all must agree)

| # | Indicator | Condition |
|---|-----------|-----------|
| 1 | Bollinger Bands | Price near or touching lower band |
| 2 | BB Width | Bands in squeeze (narrow), not in a bulge/expansion |
| 3 | RSI | RSI in 20–30 zone (oversold range) |
| 4 | MACD | At a valley / bullish crossover of signal line |
| 5 | RSI Divergence | Bullish: price making lower low, RSI making higher low |
| 6 | 200-day MA | Price above (or converging toward) 200MA — no falling knives |
| 7 | 50/200 MA | Golden cross in place or actively crossing up |
| 8 | Volume | Signal day volume above 20-day average — confirms conviction |

### Sell Conditions (all must agree)

| # | Indicator | Condition |
|---|-----------|-----------|
| 1 | Bollinger Bands | Price near or at upper band, especially with persistence |
| 2 | BB Width | Squeeze or contraction beginning after expansion |
| 3 | RSI | RSI in 70–80 zone (overbought range) |
| 4 | MACD | At a peak / bearish crossover of signal line |
| 5 | RSI Divergence | Bearish: price making higher high, RSI making lower high |
| 6 | 50/200 MA | Death cross forming (50MA crossing below 200MA) |
| 7 | Volume | Sell-off on above-average volume — confirms distribution |

### Market Context (portfolio-wide, evaluated before individual signals)

| Indicator | Rule |
|-----------|------|
| VIX / Fear & Greed | Extreme fear → buy signals carry more weight. Extreme greed → raise sell sensitivity. |
| Relative Strength vs SPY | Flag stocks underperforming SPY over 3 months. Screener prioritizes outperformers or recovering laggards. |

---

## Pillar 2 — Fundamental & News Analysis (Claude API)

This pillar produces a holistic company verdict that carries **equal weight** to the technical pillar. A strong technical signal is suppressed if this pillar returns negative.

### What Claude API Analyzes

| Category | What's Evaluated |
|----------|-----------------|
| **Recent news sentiment** | Headlines, analyst coverage, press releases — bullish / neutral / bearish |
| **Earnings** | Beat/miss vs expectations, guidance raised/maintained/cut, trend over recent quarters |
| **Revenue & growth** | YoY and QoQ revenue trend — is the business growing? |
| **Profitability** | Profitable or credible path to it; avoid chronic cash-burners |
| **Debt health** | Debt-to-equity > 2.0 flagged; extreme leverage is a long-term risk |
| **Macro context** | Fed rates, inflation, sector rotation — is the macro environment favorable? |
| **Sector momentum** | Is money flowing into or out of this sector? |
| **Competitive position** | Any major threats, market share shifts, or regulatory risks in the news? |
| **Management signals** | Leadership changes, insider buying/selling activity |

### Output
Claude API returns a structured verdict per ticker:
- **Company health score**: Healthy / Neutral / Deteriorating
- **News sentiment**: Positive / Neutral / Negative
- **Key reason**: 1–2 sentence explanation of what drove the verdict
- **Red flags**: any specific issues that warrant extra caution

### Earnings Gate (subset of this pillar)
- **Pre-earnings** (within ~2 weeks): flag to user, suggest waiting before new positions
- **Post-earnings**:
  - Beat or slight miss + guidance intact → dip is a buy candidate, run full signal check
  - Miss + guidance cut → suppress buy signals regardless of technicals

### Fundamental Hard Floors (ETFs exempt)
These are automatic disqualifiers — if any fail, no buy signal fires regardless of technicals:

| Filter | Threshold |
|--------|-----------|
| Revenue trend | No 2+ consecutive quarters of declining revenue |
| Debt-to-equity | Flag if D/E > 2.0 |
| Profitability | Profitable or credible path — no chronic cash-burners |

**Note**: TEM (Tempus AI) is early-stage — apply these filters more strictly.

---

## Position Management

### Re-evaluation Logic (replaces hard stop loss)
Price-based stops are avoided — they shake out long-term positions at the worst time.

| Situation | Action |
|-----------|--------|
| Down, both pillars still bullish | Hold. Consider averaging down to lower cost basis. |
| Down, technical signals intact but fundamentals weakening | Hold but do not add. Re-evaluate weekly. |
| Down, sell signals firing in either pillar | Exit. Thesis is broken. |
| Large loss, both pillars unclear | Hold. No forced cut. |

### Position Sizing (ATR-based)
- New positions sized by **ATR (Average True Range)** — higher volatility = smaller allocation automatically
  - Base: 10% of portfolio / ATR-normalized risk unit
  - Hard floor: 3% minimum, hard cap: 20% maximum per position
- No single stock to exceed **25% of portfolio**
- Flag when tech concentration (INTU + MSFT + XLK) exceeds 70%

### Two Operating Modes
- **DCA mode** (VOO, VXUS): monthly scheduled buys, signals irrelevant
- **Signal mode** (all individual stocks + XLK): full two-pillar stack

---

## Current Portfolio

| Ticker | Type | Shares | Avg Cost | Invested | % Portfolio |
|--------|------|--------|----------|----------|-------------|
| INTU | Individual | 3 | $468.29 | ~$1,405 | 35.8% |
| MSFT | Individual | 2 | $467.18 | ~$934 | 23.8% |
| VOO | ETF (DCA) | 1 | $602.92 | ~$603 | 15.3% |
| XLK | ETF | 2 | $142.63 | ~$285 | 7.3% |
| ABT | Individual | 2 | $109.77 | ~$220 | 5.6% |
| TEM | Individual | 3 | $69.08 | ~$207 | 5.3% |
| VXUS | ETF (DCA) | 2 | $74.24 | ~$148 | 3.8% |
| CMG | Individual | 4 | $31.47 | ~$126 | 3.2% |
| **Total** | | | | **~$3,928** | |

**Concentration flags:**
- INTU at 35.8% exceeds the 25% soft cap — monitor, don't add more until reduced
- Tech (INTU + MSFT + XLK) = ~67% — avoid adding more correlated tech names

---

## Technical Architecture

```
stock-model/
  data/               # cached OHLCV data, watchlists
  src/
    fetcher.py        # pulls price history via yfinance
    indicators.py     # Bollinger Bands, BB Width, RSI, MACD, MAs, ATR, Volume
    signals.py        # buy/sell confluence logic
    divergence.py     # RSI divergence detection
    market_context.py # VIX/Fear & Greed, relative strength vs SPY
    earnings.py       # earnings calendar + beat/miss/guidance parser
    news.py           # pulls headlines, calls Claude API for sentiment
    fundamentals.py   # revenue, D/E, profitability checks
    screener.py       # scans S&P 500 + NASDAQ 100 against buy criteria
    sizer.py          # ATR-based position sizing + suggested dollar amounts
    portfolio.py      # E*Trade API integration, holdings tracker
    planner.py        # Claude API plan generation + persistence + history
    alerts.py         # daily digest formatter + email sender
  app.py              # Streamlit frontend (5 pages)
  scheduler.py        # daily job: signals + plans + strategy + digest
  data/plans/         # persisted JSON plans (per-ticker + PORTFOLIO_STRATEGY)
  config.yaml         # tickers, thresholds, risk params, API keys
  .github/workflows/
    daily_digest.yml  # GitHub Actions — runs Mon-Fri 4:30pm ET
```

### Tech Stack

| Layer | Tool |
|-------|------|
| Language | Python |
| Price data | yfinance |
| News sentiment | Claude API |
| Frontend | Streamlit |
| Portfolio data | E*Trade API |
| Scheduling | GitHub Actions (free tier) |
| Hosting | Streamlit Community Cloud (free) |

---

## Streamlit Frontend — Key Views

1. **Strategy Dashboard** — portfolio-level health at a glance
   - Total value, overall P&L, sector allocation breakdown
   - Concentration flags (INTU > 25%, tech > 70%)
   - DCA schedule: next VOO/VXUS buy date + suggested amount
   - Market context: current VIX level, Fear & Greed reading

2. **Signal Dashboard** — what's actionable right now
   - All active signals tiered: Strong Buy / Buy / Watch / Sell / Strong Sell
   - Each signal shows: ticker, signal tier, suggested dollar amount, top 3 reasons why
   - Includes portfolio holdings flagged as average-down opportunities
   - Screener results (new candidates from S&P 500 + NASDAQ 100) shown alongside

3. **Ticker Detail** — deep dive on any stock
   - Price chart with all indicators overlaid (BB, RSI, MACD, MAs, Volume)
   - Two-pillar breakdown: technical score + fundamental/news verdict side by side
   - Recent news summary + earnings status + analyst sentiment

4. **Swing Trade Plans** — AI-generated living strategy document (3 tabs)
   - *Portfolio Strategy*: headline, market stance (Offensive/Defensive/Neutral), 4-8 week thesis, top priorities, biggest risk, what to watch, on-course checks. Stores up to 7 days of history.
   - *My Positions*: AI-generated forward plan per holding — action (Hold/Add More/Start Trimming/Exit/Wait), timeframe, entry/exit triggers, target price, risk, price at generation. Flags when action changes day-over-day. Per-ticker history (7 versions). Staleness indicator.
   - *New Opportunities*: screener-surfaced candidates with buy case, entry trigger, conviction level, suggested dollar amount.
   - All plans persist to `data/plans/` JSON and auto-regenerate daily via scheduler.

5. **Long-Term ETF Plans** — full ETF accumulation advisor (3 tabs)
   - *My ETF Holdings*: per-ETF plan with DCA signal (Normal/Increase/Accelerate), suggested dollar amount, price chart with 200MA + cost basis, Claude assessment
   - *Full ETF Universe*: all 16 ETFs across 6 categories (Broad US, International, Dividend, Growth, Sector, Factor) — quick health table with trend, drawdown, 1Y return, DCA signal
   - *New Additions*: ETFs not currently held that Claude recommends adding to the DCA stack

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-05-07 | Project initiated | MIT student needs automated stock monitoring for mid/long-term portfolio |
| 2026-05-07 | Exclude day trading scope | User's stated style; adds complexity with no benefit for their horizon |
| 2026-05-07 | Signal confluence required (all conditions) | Precision over recall; false signals are costly |
| 2026-05-07 | Signal-based exit over hard stop loss | Price stops shake out long-term positions at the worst time |
| 2026-05-07 | RSI zones 20-30 / 70-80 (not single threshold) | More realistic than 20/80 for quality stocks; still conservative |
| 2026-05-07 | Add RSI divergence | More reliable for swing/position trading than absolute RSI levels |
| 2026-05-07 | Add 200-day MA filter | Prevents buying falling knives in real downtrends |
| 2026-05-07 | Claude API for news sentiment | Nuanced judgment over keyword scoring; handles complex news context |
| 2026-05-07 | Earnings gate (2-phase) | Pre-earnings caution + post-earnings result check before acting on dips |
| 2026-05-07 | Streamlit + Streamlit Community Cloud | Python-native, fast to build, free hosting, accessible from any device |
| 2026-05-07 | GitHub Actions for daily digest | Free scheduler, sends after-market summary even when app isn't open |
| 2026-05-07 | E*Trade API for portfolio data | Live positions without manual entry; user already on E*Trade |
| 2026-05-07 | Add volume confirmation to signals | Above-average volume validates signal conviction; low-volume signals are unreliable |
| 2026-05-07 | Add 50/200 MA (golden/death cross) | Better trend context than 200MA alone; highly relevant for mid-term timeframe |
| 2026-05-07 | Add relative strength vs SPY | Avoid persistent laggards; screener prioritizes leaders or recovering stocks |
| 2026-05-07 | ATR-based position sizing | Volatility-adjusted sizing — high-vol names (e.g. TEM) get smaller allocations automatically |
| 2026-05-07 | Add VIX/Fear & Greed market context | Aligns with mean-reversion philosophy — buy signals stronger during extreme fear |
| 2026-05-07 | Screener universe: S&P 500 + NASDAQ 100 | Broad enough to find opportunities, reputable enough to match user's quality preference |
| 2026-05-07 | DCA cadence: monthly for VOO + VXUS | Regular ETF accumulation regardless of signals; monthly fits student budget/schedule |
| 2026-05-07 | Daily candles primary, weekly for confirmation | Daily gives signal granularity without intraday noise; weekly adds conviction for mid/long-term |
| 2026-05-07 | Real-time alert only for Strong Buy/Sell | End-of-day digest for everything else; avoids noise while catching high-conviction events |
| 2026-05-07 | Screener includes existing portfolio stocks | Average-down opportunities surface alongside new candidates |
| 2026-05-07 | Add per-company investment plan tracker | Each stock needs a documented thesis, entry/exit conditions, and notes — prevents emotional decisions |
| 2026-05-07 | Add strategy dashboard (portfolio-level view) | Tracks overall allocation health, concentration flags, DCA schedule, market context |
| 2026-05-07 | Two-pillar architecture: equal weight technical + fundamental/news | User wanted holistic news/fundamentals to carry real weight, not just be a filter |
| 2026-05-07 | Investment Plans rebuilt as AI-generated (not manual forms) | Tool should make plans for the user, not ask the user to fill in forms |
| 2026-05-07 | Living strategy document — persists + updates daily | User wants to refer back to a structured plan and verify they're on course |
| 2026-05-07 | Plan history — 7 daily snapshots per ticker | Track how plans evolve; surface when action recommendation changes |
| 2026-05-07 | Portfolio Strategy tab — headline + stance + thesis | Single view to understand the overall posture without reading individual plans |
| 2026-05-07 | Swing trader style confirmed (not long-term) | User clarified hold horizon is weeks to a few months, not years |
| 2026-05-07 | Scheduler regenerates plans daily alongside digest | Plans always reflect current market conditions when user opens the app |

---

## Configuration

| Setting | Value |
|---------|-------|
| Alert email | fjmulvihill@hotmail.com |
| Daily digest | End of market day (after 4pm ET) |
| Real-time alert | Strong Buy or Strong Sell only |
| DCA cadence | Monthly — VOO + VXUS |
| Screener universe | S&P 500 + NASDAQ 100 |
| Candle timeframe | Daily (primary) + Weekly (confirmation) |
| Python environment | System Python (version TBC on first run) |
| GitHub | Account confirmed — enables Actions + Streamlit Cloud |
| Anthropic API key | Confirmed — user has one |
| E*Trade API | Needs developer account setup at developer.etrade.com |

## Open Questions

- [ ] Verify current E*Trade API status post-Morgan Stanley acquisition (action: user registers at developer.etrade.com)

---

## Next Steps

**Pre-build (user action required):**
- [ ] Register at developer.etrade.com to get E*Trade API credentials

**Build order:**
1. Set up project repo, Python environment, config.yaml with API keys
2. Build fetcher.py — pull daily + weekly OHLCV via yfinance
3. Build indicators.py — BB, BB Width, RSI, MACD, 50/200 MA, ATR, Volume
4. Build divergence.py — RSI bullish/bearish divergence detection
5. Build signals.py — technical confluence logic, tiered output
6. Build market_context.py — VIX/Fear & Greed, relative strength vs SPY
7. Build fundamentals.py — revenue trend, D/E, profitability checks
8. Build earnings.py — earnings calendar, beat/miss/guidance parser
9. Build news.py — headline fetcher + Claude API sentiment analysis
10. Build sizer.py — ATR-based position sizing + suggested dollar amounts
11. Build screener.py — scan S&P 500 + NASDAQ 100, surface candidates
12. Build plans.py — per-company investment plan read/write (JSON store)
13. Wire up portfolio.py — E*Trade API integration
14. Build app.py — Streamlit frontend (5 views)
15. Build alerts.py + scheduler.py — daily digest + real-time Strong signal alerts
16. Deploy to Streamlit Community Cloud + GitHub Actions
