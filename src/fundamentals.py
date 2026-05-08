import yfinance as yf
import yaml
from pathlib import Path
from src.fetcher import fetch_info

CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))


def check_fundamentals(ticker: str, high_risk: bool = False) -> dict:
    info = fetch_info(ticker)
    flags = []
    passed = []

    # Revenue trend — use trailing vs forward revenue estimates as proxy
    total_revenue = info.get("totalRevenue")
    revenue_growth = info.get("revenueGrowth")  # YoY

    if revenue_growth is not None:
        if revenue_growth < -0.05:
            flags.append(f"Revenue declining YoY ({revenue_growth*100:.1f}%)")
        elif revenue_growth < 0:
            flags.append(f"Revenue slightly negative YoY ({revenue_growth*100:.1f}%) — watch")
        else:
            passed.append(f"Revenue growing YoY (+{revenue_growth*100:.1f}%)")
    else:
        flags.append("Revenue growth data unavailable")

    # Debt-to-equity
    de_ratio = info.get("debtToEquity")
    if de_ratio is not None:
        threshold = 150 if not high_risk else 300  # yfinance returns as %, so 2.0 = 200
        if de_ratio > threshold:
            flags.append(f"High debt-to-equity ({de_ratio/100:.1f}x)")
        else:
            passed.append(f"Debt-to-equity acceptable ({de_ratio/100:.1f}x)")
    else:
        flags.append("Debt-to-equity data unavailable")

    # Profitability
    net_income = info.get("netIncomeToCommon")
    operating_cashflow = info.get("operatingCashflow")
    profit_margins = info.get("profitMargins")

    if profit_margins is not None:
        if profit_margins > 0:
            passed.append(f"Profitable (margin {profit_margins*100:.1f}%)")
        elif operating_cashflow and operating_cashflow > 0:
            passed.append("Not net profitable but positive operating cash flow")
        else:
            flags.append("Not profitable and negative operating cash flow")
    else:
        flags.append("Profitability data unavailable")

    health = "healthy" if len(flags) == 0 else ("deteriorating" if len(flags) >= 2 else "neutral")

    return {
        "ticker": ticker,
        "health": health,
        "passed": passed,
        "flags": flags,
        "revenue_growth": revenue_growth,
        "de_ratio": de_ratio,
        "profit_margins": profit_margins,
    }
