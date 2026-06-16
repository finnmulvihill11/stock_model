from src.tier import _final_tier


def test_healthy_positive_upgrades_buy_to_strong_buy():
    assert _final_tier("Buy", "healthy", "positive", True) == "Strong Buy"


def test_healthy_positive_keeps_strong_buy():
    assert _final_tier("Strong Buy", "healthy", "positive", True) == "Strong Buy"


def test_deteriorating_fundamentals_avoids_buy():
    assert _final_tier("Buy", "deteriorating", "positive", True) == "Avoid"


def test_negative_news_avoids_buy():
    assert _final_tier("Buy", "healthy", "negative", True) == "Avoid"


def test_gate_blocked_avoids_buy():
    assert _final_tier("Buy", "healthy", "positive", False) == "Avoid"


def test_hold_unchanged_when_neutral():
    assert _final_tier("Hold", "healthy", "neutral", True) == "Hold"


def test_high_geo_risk_dampens_strong_buy_to_buy():
    assert _final_tier("Strong Buy", "healthy", "positive", True, geo_risk="high") == "Buy"


def test_high_geo_risk_dampens_buy_even_with_healthy_positive():
    # healthy+positive would promote Buy->Strong Buy, then high geo must dampen it back to Buy
    assert _final_tier("Buy", "healthy", "positive", True, geo_risk="high") == "Buy"


def test_severe_geo_kills_buy_to_hold():
    assert _final_tier("Buy", "healthy", "positive", True, geo_risk="severe") == "Hold"


def test_major_loser_blocks_sell():
    assert _final_tier("Sell", "healthy", "neutral", True, pnl_pct=-0.20) == "Hold"


def test_major_loser_does_not_block_sell_on_small_loss():
    # -14% is not a major loser (threshold is -15%)
    result = _final_tier("Sell", "healthy", "neutral", True, pnl_pct=-0.14)
    assert result == "Sell"


def test_severe_geo_and_profit_upgrades_sell_to_strong_sell():
    result = _final_tier("Sell", "healthy", "neutral", True, geo_risk="severe", pnl_pct=0.10)
    assert result == "Strong Sell"
