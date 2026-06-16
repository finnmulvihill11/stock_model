def _final_tier(tech_tier, fund_health, news_sentiment, gate_proceed, geo_risk="low", pnl_pct=None):
    original_tier = tech_tier

    # Never cut losses on major drawdowns — hold through positions down >15%
    major_loser = pnl_pct is not None and pnl_pct < -0.15
    if major_loser and tech_tier in ("Sell", "Strong Sell"):
        tech_tier = "Hold"

    if fund_health == "deteriorating" or news_sentiment == "negative" or not gate_proceed:
        return "Avoid" if tech_tier in ("Strong Buy", "Buy") else tech_tier

    # Fundamentals upgrade sets best-case conviction; geo dampening is applied after so it always wins
    if fund_health == "healthy" and news_sentiment == "positive":
        if tech_tier == "Buy":
            tech_tier = "Strong Buy"
        elif tech_tier == "Sell":
            tech_tier = "Strong Sell"

    # Geo dampens buy-side conviction — severe kills the signal entirely
    if geo_risk in ("high", "severe") and tech_tier == "Strong Buy":
        tech_tier = "Buy"
    if geo_risk == "severe" and tech_tier == "Buy":
        tech_tier = "Hold"

    # Geo amplifies an existing sell signal when in profit — never invents one from Hold
    in_profit = pnl_pct is not None and pnl_pct > 0
    if in_profit and geo_risk == "severe" and original_tier == "Sell":
        tech_tier = "Strong Sell"

    return tech_tier
