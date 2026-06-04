import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
CONFIG = yaml.safe_load(open(CONFIG_PATH))


def _reload_config() -> dict:
    return yaml.safe_load(open(CONFIG_PATH))


def _save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def update_holding_after_buy(ticker: str, shares_bought: int, buy_price: float,
                              is_dca: bool = False, high_risk: bool = False) -> dict:
    """Add or update a holding after a buy. Recalculates avg cost."""
    config = _reload_config()
    holdings = config["portfolio"]["holdings"]
    ticker = ticker.upper()

    existing = next((h for h in holdings if h["ticker"] == ticker), None)

    # If re-buying a previously sold ticker, remove it from previously_owned
    prev = config["portfolio"].get("previously_owned", [])
    if ticker in prev:
        config["portfolio"]["previously_owned"] = [t for t in prev if t != ticker]

    if existing:
        old_shares = existing["shares"]
        old_avg = existing["avg_cost"]
        new_shares = old_shares + shares_bought
        new_avg = round((old_shares * old_avg + shares_bought * buy_price) / new_shares, 2)
        existing["shares"] = new_shares
        existing["avg_cost"] = new_avg
        result = existing
    else:
        new_holding = {
            "ticker": ticker,
            "shares": shares_bought,
            "avg_cost": round(buy_price, 2),
        }
        if is_dca:
            new_holding["dca"] = True
            config["portfolio"]["dca_tickers"] = list(set(
                config["portfolio"].get("dca_tickers", []) + [ticker]
            ))
        if high_risk:
            new_holding["high_risk"] = True
        holdings.append(new_holding)
        result = new_holding

    _save_config(config)
    return result


def update_holding_after_sell(ticker: str, shares_sold: int) -> dict:
    """Reduce or remove a holding after a sell."""
    config = _reload_config()
    holdings = config["portfolio"]["holdings"]
    ticker = ticker.upper()

    existing = next((h for h in holdings if h["ticker"] == ticker), None)
    if not existing:
        return {}

    new_shares = existing["shares"] - shares_sold

    if new_shares <= 0:
        config["portfolio"]["holdings"] = [h for h in holdings if h["ticker"] != ticker]
        # Remove from swing/dca tickers lists too
        for key in ("swing_tickers", "dca_tickers"):
            if ticker in config["portfolio"].get(key, []):
                config["portfolio"][key] = [t for t in config["portfolio"][key] if t != ticker]
        # Track as previously owned so it re-enters the screener universe
        prev = config["portfolio"].get("previously_owned", [])
        if ticker not in prev:
            config["portfolio"]["previously_owned"] = prev + [ticker]
        result = {"ticker": ticker, "shares": 0, "removed": True}
    else:
        existing["shares"] = new_shares
        result = existing

    _save_config(config)
    return result


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
