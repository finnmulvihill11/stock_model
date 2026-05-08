import json
from pathlib import Path
from datetime import datetime

PLANS_DIR = Path(__file__).parent.parent / "data" / "plans"
PLANS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PLAN = {
    "ticker": "",
    "thesis": "",
    "entry_conditions": "",
    "exit_conditions": "",
    "target_price_low": None,
    "target_price_high": None,
    "expected_hold": "",
    "status": "watching",  # watching | holding | considering_exit | closed
    "notes": "",
    "created": "",
    "updated": "",
}


def get_plan(ticker: str) -> dict:
    path = PLANS_DIR / f"{ticker.upper()}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    plan = DEFAULT_PLAN.copy()
    plan["ticker"] = ticker.upper()
    plan["created"] = datetime.now().isoformat()
    return plan


def save_plan(ticker: str, plan: dict) -> None:
    plan["ticker"] = ticker.upper()
    plan["updated"] = datetime.now().isoformat()
    if not plan.get("created"):
        plan["created"] = plan["updated"]
    path = PLANS_DIR / f"{ticker.upper()}.json"
    with open(path, "w") as f:
        json.dump(plan, f, indent=2)


def list_plans() -> list[dict]:
    plans = []
    for path in sorted(PLANS_DIR.glob("*.json")):
        with open(path) as f:
            plans.append(json.load(f))
    return plans


def delete_plan(ticker: str) -> bool:
    path = PLANS_DIR / f"{ticker.upper()}.json"
    if path.exists():
        path.unlink()
        return True
    return False
