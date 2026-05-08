# Stock Model — Project Context for Claude

## What This Project Is

A personal stock analysis and decision-support tool for a busy investor who doesn't have time to monitor charts daily. The tool should surface buy/sell signals automatically and abstract away the manual chart-checking workflow.

## Owner Profile

- MIT student — time is scarce, automation is the whole point
- Mid-term to long-term investor (hold horizon: months to years)
- NOT a day trader — no scalping, no intraday signals
- Holds index funds (e.g. VOO) alongside individual equities
- Already has a personal set of buy/sell criteria; wants to encode those first, then layer in additional analysis

## Core Goals

1. **Automate existing criteria** — encode the user's current buy/sell rules as executable logic
2. **Stock screening** — surface candidates worth looking at (what to buy)
3. **Signal generation** — tell the user when to act on a position (when to buy/sell)
4. **Position sizing** — suggest how much to allocate given portfolio context
5. **Low-maintenance operation** — run on a schedule, alert the user, require minimal daily interaction

## Trading Style Constraints

- Time horizon: weeks to years (not hours)
- Risk profile: growth-oriented but not reckless; index fund anchor suggests moderate risk tolerance
- Instruments: US equities + ETFs to start
- No leverage, no options (unless user adds later)

## Technical Approach (TBD — evolve this as decisions are made)

- Language: Python
- Data: TBD (yfinance, Alpha Vantage, Polygon, etc.)
- Signals: technical indicators + any fundamental filters the user defines
- Delivery: TBD (CLI output, email alert, dashboard, etc.)

## Key Design Principles

- The user's own rules come first — don't override their judgment, augment it
- Signals should be explainable — show WHY a signal fired, not just that it did
- False positives are costly (user acts on bad signals) — prefer precision over recall
- Keep it runnable on a schedule without babysitting

## Files of Note

- `PDR.md` — full project design record with requirements and feature breakdown
- `CLAUDE.md` — this file; Claude's working memory for the project
