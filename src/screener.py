import json
import pandas as pd
import yaml
from datetime import datetime
from pathlib import Path
from src.signals import get_technical_signal
from src.market_context import get_relative_strength

CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
CACHE_FILE = Path(__file__).parent.parent / "data" / "screener_cache.json"
OVERRIDES_FILE = Path(__file__).parent.parent / "data" / "screener_overrides.json"

SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
TICKER_CACHE = Path(__file__).parent.parent / "data" / "ticker_universe.json"

# NASDAQ 100 supplement — companies in NASDAQ 100 not typically in S&P 500
# Dead tickers are auto-removed at runtime via screener_overrides.json
NASDAQ100_EXTRA = [
    "ADBE", "AMD", "ABNB", "ALGN", "GOOGL", "GOOG", "AMZN", "AMGN", "AEP",
    "ADI", "AAPL", "AMAT", "ASML", "AZN", "TEAM", "ADSK", "BKR",
    "BIIB", "BKNG", "AVGO", "CDNS", "CDW", "CHTR", "CTAS", "CSCO", "CCEP",
    "CTSH", "CMCSA", "CEG", "CPRT", "CSGP", "COST", "CRWD", "CSX", "DDOG",
    "DXCM", "FANG", "DLTR", "EBAY", "EA", "EXC", "FAST", "FTNT", "GEHC",
    "GILD", "GFS", "HON", "IDXX", "ILMN", "INTC", "INTU", "ISRG", "KDP",
    "KLAC", "KHC", "LRCX", "LULU", "MAR", "MRVL", "MELI", "META", "MCHP",
    "MU", "MSFT", "MRNA", "MDLZ", "MCD", "MNST", "NFLX", "NVDA", "NXPI",
    "ODFL", "ON", "PCAR", "PANW", "PAYX", "PYPL", "PDD", "PEP", "QCOM",
    "REGN", "ROST", "SIRI", "SBUX", "SNPS", "TTWO", "TMUS", "TSLA",
    "TXN", "TTD", "VRSK", "VRTX", "WBD", "WDAY", "XEL", "ZM", "ZS",
]

# Reserve pool — pulled in to replace confirmed dead tickers
RESERVE_POOL = [
    "PLTR", "HOOD", "COIN", "RBLX", "DKNG", "PINS", "ROKU", "SNAP",
    "LYFT", "AFRM", "SOFI", "NU", "DUOL", "CELH", "GTLB", "BILL",
    "SMAR", "U", "TTWO", "APP", "BROS", "RXRX", "IONQ", "ACHR",
]


# ── Override helpers ──────────────────────────────────────────────────────────

def _load_overrides() -> dict:
    if OVERRIDES_FILE.exists():
        try:
            return json.loads(OVERRIDES_FILE.read_text())
        except Exception:
            pass
    # Seed with known dead tickers so they're skipped immediately
    return {
        "dead_tickers": ["SGEN", "ANSS", "WBA", "NYCB"],
        "added_tickers": [],
        "updated_at": None,
    }


def _save_overrides(data: dict) -> None:
    OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now().isoformat()
    OVERRIDES_FILE.write_text(json.dumps(data, indent=2))


def _mark_dead_and_replace(dead_ticker: str, current_universe: set) -> str | None:
    """
    Add dead_ticker to the override dead list and find the next unused
    reserve ticker to add to the universe. Returns the replacement or None.
    """
    overrides = _load_overrides()
    if dead_ticker in overrides["dead_tickers"]:
        return None  # already known dead

    overrides["dead_tickers"].append(dead_ticker)
    used = set(overrides["dead_tickers"]) | set(overrides["added_tickers"]) | current_universe
    replacement = next((t for t in RESERVE_POOL if t not in used), None)
    if replacement:
        overrides["added_tickers"].append(replacement)
    _save_overrides(overrides)
    return replacement


# ── Universe builders ─────────────────────────────────────────────────────────

def _get_sp500_tickers() -> list[str]:
    if TICKER_CACHE.exists():
        try:
            cached = json.loads(TICKER_CACHE.read_text())
            age_h = (datetime.now() - datetime.fromisoformat(cached["saved_at"])).total_seconds() / 3600
            if age_h < 168:
                return cached["tickers"]
        except Exception:
            pass

    try:
        import requests as _req
        resp = _req.get(SP500_CSV, timeout=10)
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")[1:]
        tickers = [line.split(",")[0].strip().replace(".", "-") for line in lines if line]
        TICKER_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TICKER_CACHE.write_text(json.dumps({"saved_at": datetime.now().isoformat(), "tickers": tickers}))
        return tickers
    except Exception as e:
        print(f"  S&P 500 fetch failed: {e} — using cached or empty list")
        return []


def _get_nasdaq100_tickers() -> list[str]:
    overrides = _load_overrides()
    dead = set(overrides.get("dead_tickers", []))
    added = overrides.get("added_tickers", [])
    tickers = [t for t in NASDAQ100_EXTRA if t not in dead]
    tickers.extend(t for t in added if t not in tickers)
    return tickers


def _get_portfolio_tickers() -> list[str]:
    holdings = CONFIG.get("portfolio", {}).get("holdings", [])
    return [h["ticker"] for h in holdings]


def _get_previously_owned_tickers() -> list[str]:
    return CONFIG.get("portfolio", {}).get("previously_owned", [])


# ── Scraper ───────────────────────────────────────────────────────────────────

def run_full_scrape(verbose: bool = False) -> list[dict]:
    """Full weekly scrape — scans S&P 500 + NASDAQ 100 supplement.
    Dead tickers are auto-removed and replaced from the reserve pool."""
    from src.watchlist import auto_add_strong_signals

    prev_cache = load_cache()
    previous_tickers = {r["ticker"] for r in prev_cache.get("results", [])
                        if r.get("tier") in ("Strong Buy", "Buy")}

    universes = CONFIG["screener"]["universes"]
    tickers = set()
    if "sp500" in universes:
        sp = _get_sp500_tickers()
        tickers.update(sp)
        if verbose:
            print(f"S&P 500: {len(sp)} tickers")
    if "nasdaq100" in universes:
        nq = _get_nasdaq100_tickers()
        tickers.update(nq)
        if verbose:
            print(f"NASDAQ 100 supplement: {len(nq)} tickers")
    if CONFIG["screener"].get("include_portfolio", True):
        tickers.update(_get_portfolio_tickers())

    previously_owned_set = set(_get_previously_owned_tickers())
    tickers.update(previously_owned_set)

    tickers = list(tickers)
    if verbose:
        print(f"Total universe: {len(tickers)} tickers")

    results = []
    for i, ticker in enumerate(tickers):
        try:
            signal = get_technical_signal(ticker)
            tier = signal["tier"]
            if tier in ("Strong Buy", "Buy"):
                rs = get_relative_strength(ticker)
                signal["relative_strength"] = rs.get("relative_strength")
                signal["rs_label"] = rs.get("label")
                signal["previously_owned"] = ticker in previously_owned_set
                results.append(signal)
                if verbose:
                    print(f"  [{i+1}/{len(tickers)}] {ticker}: {tier}")
        except ValueError as e:
            # ValueError from fetcher = no data returned = likely delisted
            replacement = _mark_dead_and_replace(ticker, set(tickers))
            if replacement:
                tickers.append(replacement)  # scan replacement this run
                if verbose:
                    print(f"  [{i+1}/{len(tickers)}] {ticker}: delisted -> added {replacement} to universe")
            elif verbose:
                print(f"  [{i+1}/{len(tickers)}] {ticker}: delisted, reserve pool exhausted")
        except Exception as e:
            if verbose:
                print(f"  [{i+1}/{len(tickers)}] {ticker}: skip ({e})")
            continue

    results.sort(key=lambda x: (
        ["Strong Buy", "Buy"].index(x["tier"]) if x["tier"] in ["Strong Buy", "Buy"] else 99,
        -(x.get("relative_strength") or 0),
    ))

    _save_cache(results)

    if verbose:
        print("Checking watchlist auto-add...")
    portfolio_tickers = set(_get_portfolio_tickers())
    candidates = [r for r in results if r["ticker"] not in portfolio_tickers]
    auto_added = auto_add_strong_signals(candidates, previous_tickers)
    if verbose:
        print(f"Auto-added {len(auto_added)} tickers to watchlist: {auto_added}")
        print(f"Screener complete: {len(results)} opportunities found")

    return results


def _save_cache(results: list[dict]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "count": len(results),
            "results": results,
        }, f, indent=2)


def load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {"results": [], "generated_at": None, "count": 0, "stale": True}
    with open(CACHE_FILE) as f:
        data = json.load(f)
    try:
        age_hours = (datetime.now() - datetime.fromisoformat(data["generated_at"])).total_seconds() / 3600
        data["age_hours"] = round(age_hours, 1)
        data["stale"] = age_hours > 25
    except Exception:
        data["age_hours"] = None
        data["stale"] = True
    return data


def run_screener(max_results: int = None) -> list[dict]:
    max_results = max_results or CONFIG["screener"]["max_results"]
    cache = load_cache()
    results = cache.get("results", [])
    portfolio_tickers = set(_get_portfolio_tickers())
    cached_tickers = {r["ticker"] for r in results}
    for ticker in portfolio_tickers:
        if ticker not in cached_tickers:
            try:
                signal = get_technical_signal(ticker)
                if signal["tier"] in ("Strong Buy", "Buy"):
                    results.append(signal)
            except Exception:
                pass
    return results[:max_results]
