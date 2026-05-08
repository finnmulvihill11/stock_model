import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))


def get_portfolio_from_config() -> list[dict]:
    """Fallback: load portfolio from config.yaml when E*Trade is unavailable."""
    import yfinance as yf
    holdings = CONFIG["portfolio"]["holdings"]
    result = []
    for h in holdings:
        ticker = h["ticker"]
        try:
            info = yf.Ticker(ticker).fast_info
            current_price = float(info.last_price)
        except Exception:
            current_price = h["avg_cost"]
        shares = h["shares"]
        avg_cost = h["avg_cost"]
        value = shares * current_price
        cost_basis = shares * avg_cost
        result.append({
            "ticker": ticker,
            "shares": shares,
            "avg_cost": avg_cost,
            "current_price": round(current_price, 2),
            "value": round(value, 2),
            "cost_basis": round(cost_basis, 2),
            "unrealized_pnl": round(value - cost_basis, 2),
            "unrealized_pnl_pct": round((value - cost_basis) / cost_basis * 100, 2),
            "dca": h.get("dca", False),
            "high_risk": h.get("high_risk", False),
            "source": "config",
        })
    return result


def get_portfolio_summary(holdings: list[dict]) -> dict:
    total_value = sum(h["value"] for h in holdings)
    total_cost = sum(h["cost_basis"] for h in holdings)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    tech_tickers = CONFIG["portfolio"]["tech_concentration_tickers"]
    tech_value = sum(h["value"] for h in holdings if h["ticker"] in tech_tickers)
    tech_pct = tech_value / total_value if total_value > 0 else 0

    flags = []
    max_pct = CONFIG["portfolio"]["max_position_pct"]
    for h in holdings:
        pct = h["value"] / total_value if total_value > 0 else 0
        h["portfolio_pct"] = round(pct * 100, 1)
        if pct > max_pct and not h.get("dca"):
            flags.append(f"{h['ticker']} at {pct*100:.1f}% — exceeds {max_pct*100:.0f}% cap")

    tech_limit = CONFIG["portfolio"]["tech_concentration_limit"]
    if tech_pct > tech_limit:
        flags.append(f"Tech concentration at {tech_pct*100:.1f}% — limit is {tech_limit*100:.0f}%")

    return {
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "tech_pct": round(tech_pct * 100, 1),
        "flags": flags,
        "holdings": holdings,
    }


def get_portfolio() -> dict:
    # E*Trade integration goes here once credentials are set up.
    # For now, fall back to config.yaml.
    consumer_key = os.getenv("ETRADE_CONSUMER_KEY", "")
    if consumer_key and consumer_key != "your_etrade_consumer_key_here":
        pass  # TODO: wire up E*Trade OAuth flow
    holdings = get_portfolio_from_config()
    return get_portfolio_summary(holdings)
