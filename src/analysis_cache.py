"""
Disk-based cache for full per-ticker analysis results.
Scheduler writes here nightly. App reads from here — no on-demand computation.
"""
import json
from datetime import datetime
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "data" / "analysis"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

OPPS_FILE = Path(__file__).parent.parent / "data" / "opportunity_plans.json"


def save_ticker_analysis(ticker: str, analysis: dict) -> None:
    analysis["saved_at"] = datetime.now().isoformat()
    path = CACHE_DIR / f"{ticker.upper()}.json"
    with open(path, "w") as f:
        json.dump(analysis, f, indent=2)


def load_ticker_analysis(ticker: str) -> dict | None:
    path = CACHE_DIR / f"{ticker.upper()}.json"
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    try:
        age_h = (datetime.now() - datetime.fromisoformat(data["saved_at"])).total_seconds() / 3600
        data["age_hours"] = round(age_h, 1)
        data["stale"] = age_h > 25
    except Exception:
        data["age_hours"] = None
        data["stale"] = True
    return data


def load_all_analyses(tickers: list[str]) -> dict[str, dict]:
    return {t: load_ticker_analysis(t) for t in tickers}


def save_opportunity_plans(plans: list[dict]) -> None:
    with open(OPPS_FILE, "w") as f:
        json.dump({"saved_at": datetime.now().isoformat(), "plans": plans}, f, indent=2)


def load_opportunity_plans() -> dict:
    if not OPPS_FILE.exists():
        return {"plans": [], "saved_at": None, "stale": True}
    with open(OPPS_FILE) as f:
        data = json.load(f)
    try:
        age_h = (datetime.now() - datetime.fromisoformat(data["saved_at"])).total_seconds() / 3600
        data["age_hours"] = round(age_h, 1)
        data["stale"] = age_h > 168  # weekly cadence
    except Exception:
        data["age_hours"] = None
        data["stale"] = True
    return data


ETF_UNIVERSE_FILE = Path(__file__).parent.parent / "data" / "etf_universe_analysis.json"
ETF_OPPS_FILE = Path(__file__).parent.parent / "data" / "etf_opportunity_plans.json"


def save_etf_universe_analysis(results: list[dict]) -> None:
    with open(ETF_UNIVERSE_FILE, "w") as f:
        json.dump({"saved_at": datetime.now().isoformat(), "results": results}, f, indent=2)


def load_etf_universe_analysis() -> dict:
    if not ETF_UNIVERSE_FILE.exists():
        return {"results": [], "saved_at": None, "stale": True}
    with open(ETF_UNIVERSE_FILE) as f:
        data = json.load(f)
    try:
        age_h = (datetime.now() - datetime.fromisoformat(data["saved_at"])).total_seconds() / 3600
        data["age_hours"] = round(age_h, 1)
        data["stale"] = age_h > 25
    except Exception:
        data["stale"] = True
    return data


def save_etf_opportunity_plans(plans: list[dict]) -> None:
    with open(ETF_OPPS_FILE, "w") as f:
        json.dump({"saved_at": datetime.now().isoformat(), "plans": plans}, f, indent=2)


def load_etf_opportunity_plans() -> dict:
    if not ETF_OPPS_FILE.exists():
        return {"plans": [], "saved_at": None, "stale": True}
    with open(ETF_OPPS_FILE) as f:
        data = json.load(f)
    try:
        age_h = (datetime.now() - datetime.fromisoformat(data["saved_at"])).total_seconds() / 3600
        data["age_hours"] = round(age_h, 1)
        data["stale"] = age_h > 25
    except Exception:
        data["stale"] = True
    return data


def get_cache_status(tickers: list[str]) -> dict:
    """Summary of how fresh the overnight cache is."""
    ages = []
    missing = []
    for t in tickers:
        a = load_ticker_analysis(t)
        if a is None:
            missing.append(t)
        elif a.get("age_hours"):
            ages.append(a["age_hours"])

    if not ages and missing:
        return {"status": "empty", "message": "No overnight data yet — scheduler hasn't run.", "oldest_h": None}

    oldest = max(ages) if ages else None
    if missing:
        return {"status": "partial", "message": f"Missing data for: {', '.join(missing)}", "oldest_h": oldest}
    if oldest and oldest > 25:
        return {"status": "stale", "message": f"Data is {oldest:.0f}h old — will refresh tonight.", "oldest_h": oldest}

    return {"status": "fresh", "message": f"Updated {min(ages):.0f}–{oldest:.0f}h ago.", "oldest_h": oldest}
