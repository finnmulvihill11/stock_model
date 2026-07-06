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


def record_allocation(ticker: str, shares: float, amount: float, trade_type: str) -> None:
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


def get_buy_log() -> list[dict]:
    data = _load()
    return [a for a in data.get("allocations", []) if a.get("action") == "buy"]


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


_CONVICTION_MULTIPLIERS = {
    "Strong Buy": 1.0,
    "Buy": 0.60,
}


def size_swing_trade(
    ticker: str,
    current_price: float,
    atr: float,
    tier: str = "Buy",
    portfolio_value: float = 0.0,
    current_position_value: float = 0.0,
) -> dict:
    """
    Size a swing trade with a hard per-trade dollar ceiling and conviction scaling.

    Ceiling = max_swing_trade_pct (30%) of remaining budget × conviction multiplier.
    ATR determines share count within that ceiling.
    Strong Buy gets full ceiling; Buy gets 60%.
    """
    remaining = get_swing_budget_remaining()
    if remaining <= 0 or current_price <= 0:
        return {"shares": 0, "amount": 0, "note": "No swing budget remaining"}

    # Concentration guard
    if portfolio_value > 0:
        max_pos = portfolio_value * CONFIG["sizing"]["max_position_pct"]
        if current_position_value >= max_pos:
            return {"shares": 0, "amount": 0,
                    "note": f"Position already at {CONFIG['sizing']['max_position_pct']*100:.0f}% portfolio cap"}

    multiplier = _CONVICTION_MULTIPLIERS.get(tier, 0.60)
    max_trade_pct = CONFIG["sizing"].get("max_swing_trade_pct", 0.30)

    # Hard dollar ceiling for this trade
    dollar_ceiling = remaining * max_trade_pct * multiplier

    # Trim further if portfolio concentration room is tighter
    if portfolio_value > 0:
        room = max(portfolio_value * CONFIG["sizing"]["max_position_pct"] - current_position_value, 0)
        dollar_ceiling = min(dollar_ceiling, room)

    # ATR-based position dollars (risk tolerance → share count → dollar value)
    risk_per_share = max(atr * CONFIG["sizing"]["atr_multiplier"], current_price * 0.02)
    atr_shares = int((remaining * CONFIG["sizing"]["base_risk_pct"] * multiplier) / risk_per_share)
    atr_dollars = atr_shares * current_price

    # Take the more conservative of ATR-based and hard ceiling
    target_dollars = min(atr_dollars, dollar_ceiling)
    shares = int(target_dollars / current_price)

    # Strong Buy only: guarantee at least 1 share if stock is affordable
    over_ceiling = False
    if shares == 0 and tier == "Strong Buy" and current_price <= remaining:
        shares = 1
        over_ceiling = True

    if shares == 0:
        return {"shares": 0, "amount": 0,
                "note": f"Price exceeds {int(multiplier*100)}% conviction ceiling (${dollar_ceiling:.0f}) — skip or wait for Strong Buy"}

    amount = round(shares * current_price, 2)
    conviction_label = "full size" if multiplier == 1.0 else f"{int(multiplier*100)}% size"
    ceiling_note = " — 1 share minimum, price above normal ceiling" if over_ceiling else ""
    note = (
        f"{shares} sh @ ${current_price:.2f} = ${amount:,.0f} "
        f"({tier}, {conviction_label}{ceiling_note}) | "
        f"${remaining - amount:,.0f} swing budget after"
    )

    return {
        "shares": shares,
        "amount": amount,
        "note": note,
        "budget_remaining": round(remaining, 2),
        "conviction_pct": int(multiplier * 100),
    }
