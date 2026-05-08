import json
import yaml
from datetime import datetime, date, timedelta
from pathlib import Path

CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
BUDGET_FILE = Path(__file__).parent.parent / "data" / "budget.json"
BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)

B = CONFIG["budget"]
EA = CONFIG["etf_advisor"]


def _load() -> dict:
    if BUDGET_FILE.exists():
        with open(BUDGET_FILE) as f:
            return json.load(f)
    return {
        "total": B["total"],
        "period_months": B["period_months"],
        "start_date": B["start_date"],
        "allocations": [],
        "spent": 0,
        "spent_etf": 0,
        "spent_swing": 0,
        "remaining": B["total"],
        "pct_used": 0,
    }


def _save(data: dict) -> None:
    with open(BUDGET_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_budget_state() -> dict:
    data = _load()
    total = data["total"]
    start = datetime.fromisoformat(data["start_date"]).date()
    end = date(
        start.year + ((start.month - 1 + data["period_months"]) // 12),
        ((start.month - 1 + data["period_months"]) % 12) + 1,
        start.day,
    )
    today = date.today()
    days_remaining = max((end - today).days, 0)
    months_remaining = round(days_remaining / 30.4, 1)

    allocations = data.get("allocations", [])
    spent_total = sum(a["amount"] for a in allocations)
    spent_etf = sum(a["amount"] for a in allocations if a.get("type") == "etf")
    spent_swing = sum(a["amount"] for a in allocations if a.get("type") == "swing")
    remaining = total - spent_total

    return {
        "total": total,
        "spent": round(spent_total, 2),
        "remaining": round(remaining, 2),
        "spent_etf": round(spent_etf, 2),
        "spent_swing": round(spent_swing, 2),
        "start_date": str(start),
        "end_date": str(end),
        "days_remaining": days_remaining,
        "months_remaining": months_remaining,
        "allocations": allocations,
        "pct_used": round(spent_total / total * 100, 1) if total > 0 else 0,
    }


def record_allocation(ticker: str, shares: int, amount: float, trade_type: str) -> None:
    """Record a purchase. trade_type: 'swing' or 'etf'"""
    data = _load()
    data["allocations"].append({
        "date": str(date.today()),
        "ticker": ticker,
        "shares": shares,
        "amount": round(amount, 2),
        "type": trade_type,
        "action": "buy",
    })
    _save(data)


def record_sell(ticker: str, shares: int, sell_price: float, avg_cost: float) -> dict:
    """Record a sell. Returns realized P&L. Proceeds go back into swing budget pool."""
    proceeds = round(shares * sell_price, 2)
    cost_basis = round(shares * avg_cost, 2)
    realized_pnl = round(proceeds - cost_basis, 2)
    realized_pnl_pct = round((proceeds - cost_basis) / cost_basis * 100, 2) if cost_basis > 0 else 0

    data = _load()
    # Record as negative spend — puts proceeds back into the pool
    data["allocations"].append({
        "date": str(date.today()),
        "ticker": ticker,
        "shares": shares,
        "amount": -proceeds,   # negative = money back in
        "type": "swing",
        "action": "sell",
        "sell_price": sell_price,
        "avg_cost": avg_cost,
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
    })
    _save(data)

    return {
        "proceeds": proceeds,
        "cost_basis": cost_basis,
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
    }


def get_sell_log() -> list[dict]:
    data = _load()
    return [a for a in data.get("allocations", []) if a.get("action") == "sell"]


def get_swing_budget_remaining() -> float:
    """Remaining swing budget = swing allocation minus net swing spend (buys - sell proceeds)."""
    data = _load()
    swing_total = B.get("swing_budget", B["total"] // 2)
    # Net spend = buys - sell proceeds (sells are stored as negative amounts)
    net_swing_spend = sum(a["amount"] for a in data.get("allocations", []) if a.get("type") == "swing")
    return max(swing_total - net_swing_spend, 0)


def get_etf_budget_remaining() -> float:
    """Remaining ETF budget = ETF allocation minus what's already been spent on ETFs."""
    state = get_budget_state()
    etf_total = B.get("etf_budget", B["total"] // 2)
    return max(etf_total - state["spent_etf"], 0)


def get_etf_dca_schedule(current_price: float) -> dict:
    """Return how many shares to buy this month for a DCA ETF."""
    threshold = EA["high_cost_threshold"]
    today = date.today()

    if current_price > threshold:
        freq = EA["high_cost_freq_months"]
        # Buy in odd months of the period (month 1, 3, 5...)
        period_month = ((today.month - datetime.fromisoformat(B["start_date"]).month) % 12) + 1
        should_buy = (period_month % freq) == 1
        shares = 1 if should_buy else 0
        label = f"1 share every {freq} months"
    else:
        shares = 1
        label = "1 share every month"

    return {
        "shares": shares,
        "amount": round(shares * current_price, 2),
        "label": label,
        "buy_this_month": shares > 0,
    }


def size_swing_trade(ticker: str, current_price: float, atr: float) -> dict:
    """
    Size a swing trade in whole shares, respecting remaining swing budget.
    Returns 0 shares if budget is exhausted.
    """
    remaining = get_swing_budget_remaining()
    if remaining <= 0 or current_price <= 0:
        return {"shares": 0, "amount": 0, "note": "No swing budget remaining"}

    # ATR-based ideal size
    risk_per_share = max(atr * CONFIG["sizing"]["atr_multiplier"], current_price * 0.02)
    risk_amount = remaining * CONFIG["sizing"]["base_risk_pct"]
    ideal_shares = max(1, int(risk_amount / risk_per_share))

    # Cap to budget and max position
    max_affordable = int(remaining / current_price)
    shares = min(ideal_shares, max_affordable)
    shares = max(shares, 0)

    amount = round(shares * current_price, 2)
    note = f"{shares} shares @ ${current_price:.2f} = ${amount:,.0f} | ${remaining - amount:,.0f} swing budget remaining after"

    return {"shares": shares, "amount": amount, "note": note, "budget_remaining": round(remaining, 2)}
