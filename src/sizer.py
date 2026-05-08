import yaml
from pathlib import Path

CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
SZ = CONFIG["sizing"]


def calculate_position_size(
    ticker: str,
    portfolio_value: float,
    current_price: float,
    atr: float,
    current_holdings_value: float = 0.0,
) -> dict:
    """
    ATR-based position sizing. Risk per trade = base_risk_pct of portfolio.
    Position size = risk amount / (ATR * atr_multiplier).
    """
    if atr <= 0 or current_price <= 0:
        return {"ticker": ticker, "suggested_shares": 0, "suggested_dollars": 0, "note": "Invalid price/ATR"}

    risk_amount = portfolio_value * SZ["base_risk_pct"]
    risk_per_share = atr * SZ["atr_multiplier"]
    raw_shares = risk_amount / risk_per_share
    raw_dollars = raw_shares * current_price

    # Apply portfolio percentage caps
    min_dollars = portfolio_value * SZ["min_position_pct"]
    max_dollars = portfolio_value * SZ["max_position_pct"]

    # Account for existing position
    remaining_room = max_dollars - current_holdings_value
    if remaining_room <= 0:
        return {
            "ticker": ticker,
            "suggested_shares": 0,
            "suggested_dollars": 0,
            "note": f"Position at or above {SZ['max_position_pct']*100:.0f}% cap",
        }

    suggested_dollars = min(max(raw_dollars, min_dollars), remaining_room)
    suggested_shares = int(suggested_dollars / current_price)

    return {
        "ticker": ticker,
        "suggested_shares": suggested_shares,
        "suggested_dollars": round(suggested_dollars, 2),
        "risk_per_share": round(risk_per_share, 2),
        "note": f"ATR-based sizing: {suggested_shares} shares @ ${current_price:.2f}",
    }
