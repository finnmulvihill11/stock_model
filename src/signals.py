import pandas as pd
import yaml
from pathlib import Path
from src.fetcher import fetch_ohlcv, fetch_ohlcv_weekly
from src.indicators import add_all_indicators
from src.divergence import detect_rsi_divergence

CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
IND = CONFIG["indicators"]

TIERS = ["Strong Buy", "Buy", "Watch", "Hold", "Avoid", "Sell", "Strong Sell"]


def _evaluate_buy_conditions(df: pd.DataFrame, div: dict) -> tuple[list[str], list[str]]:
    row = df.iloc[-1]
    passed = []
    failed = []

    if row["bb_pct"] <= 0.20:
        passed.append(f"BB: price near lower band (pct={row['bb_pct']:.2f})")
    else:
        failed.append(f"BB: price not near lower band (pct={row['bb_pct']:.2f})")

    bb_width_ma = df["bb_width"].rolling(50).mean().iloc[-1]
    if row["bb_width"] < bb_width_ma:
        passed.append(f"BB Width: squeeze active (width={row['bb_width']:.4f})")
    else:
        failed.append(f"BB Width: bands expanding, not squeezing")

    if IND["rsi_buy_low"] <= row["rsi"] <= IND["rsi_buy_high"]:
        passed.append(f"RSI: oversold zone ({row['rsi']:.1f})")
    else:
        failed.append(f"RSI: not in buy zone ({row['rsi']:.1f}, need {IND['rsi_buy_low']}-{IND['rsi_buy_high']})")

    hist_prev = df["macd_hist"].iloc[-2]
    hist_curr = df["macd_hist"].iloc[-1]
    if hist_prev < 0 and hist_curr > hist_prev:
        passed.append(f"MACD: histogram at valley / turning up")
    elif df["macd"].iloc[-1] > df["macd_signal"].iloc[-1] and df["macd"].iloc[-2] <= df["macd_signal"].iloc[-2]:
        passed.append(f"MACD: bullish crossover")
    else:
        failed.append(f"MACD: no bullish crossover or valley")

    if div["bullish"]:
        passed.append(f"RSI Divergence: {div['bull_reason']}")
    else:
        failed.append("RSI Divergence: no bullish divergence detected")

    ma200 = row.get("ma200")
    if pd.notna(ma200) and row["Close"] >= ma200 * 0.97:
        passed.append(f"200MA: price at/above 200MA ({row['Close']:.2f} vs {ma200:.2f})")
    else:
        failed.append(f"200MA: price below 200MA — potential falling knife")

    ma50, ma200 = row.get("ma50"), row.get("ma200")
    if pd.notna(ma50) and pd.notna(ma200):
        if ma50 >= ma200:
            passed.append(f"Golden cross: 50MA ({ma50:.2f}) above 200MA ({ma200:.2f})")
        elif (df["ma50"].iloc[-2] - df["ma200"].iloc[-2]) / df["ma200"].iloc[-2] < -0.01 and ma50 > df["ma50"].iloc[-5]:
            passed.append("50/200MA: converging upward toward golden cross")
        else:
            failed.append(f"Death cross active: 50MA ({ma50:.2f}) below 200MA ({ma200:.2f})")
    else:
        failed.append("50/200MA: insufficient history")

    if row["volume_ratio"] >= 1.0:
        passed.append(f"Volume: above average ({row['volume_ratio']:.2f}x)")
    else:
        failed.append(f"Volume: below average ({row['volume_ratio']:.2f}x) — low conviction")

    return passed, failed


def _evaluate_sell_conditions(df: pd.DataFrame, div: dict) -> tuple[list[str], list[str]]:
    row = df.iloc[-1]
    passed = []
    failed = []

    if row["bb_pct"] >= 0.80:
        passed.append(f"BB: price near upper band (pct={row['bb_pct']:.2f})")
    else:
        failed.append(f"BB: price not near upper band (pct={row['bb_pct']:.2f})")

    bb_width_ma = df["bb_width"].rolling(50).mean().iloc[-1]
    if row["bb_width"] < bb_width_ma:
        passed.append("BB Width: squeeze/contraction after expansion")
    else:
        failed.append("BB Width: bands still expanding")

    if IND["rsi_sell_low"] <= row["rsi"] <= IND["rsi_sell_high"]:
        passed.append(f"RSI: overbought zone ({row['rsi']:.1f})")
    else:
        failed.append(f"RSI: not in sell zone ({row['rsi']:.1f}, need {IND['rsi_sell_low']}-{IND['rsi_sell_high']})")

    hist_prev = df["macd_hist"].iloc[-2]
    hist_curr = df["macd_hist"].iloc[-1]
    if hist_prev > 0 and hist_curr < hist_prev:
        passed.append("MACD: histogram at peak / turning down")
    elif df["macd"].iloc[-1] < df["macd_signal"].iloc[-1] and df["macd"].iloc[-2] >= df["macd_signal"].iloc[-2]:
        passed.append("MACD: bearish crossover")
    else:
        failed.append("MACD: no bearish crossover or peak")

    if div["bearish"]:
        passed.append(f"RSI Divergence: {div['bear_reason']}")
    else:
        failed.append("RSI Divergence: no bearish divergence detected")

    ma50, ma200 = row.get("ma50"), row.get("ma200")
    if pd.notna(ma50) and pd.notna(ma200):
        if ma50 < ma200:
            passed.append(f"Death cross: 50MA ({ma50:.2f}) below 200MA ({ma200:.2f})")
        elif df["ma50"].iloc[-5] > df["ma200"].iloc[-5] and (ma50 - ma200) / ma200 < 0.02:
            passed.append("50/200MA: converging downward toward death cross")
        else:
            failed.append(f"Golden cross still active: 50MA above 200MA")

    if row["volume_ratio"] >= 1.0:
        passed.append(f"Volume: above average on sell ({row['volume_ratio']:.2f}x) — confirms distribution")
    else:
        failed.append(f"Volume: below average ({row['volume_ratio']:.2f}x)")

    return passed, failed


def _tier_from_counts(passed: int, total: int, direction: str) -> str:
    ratio = passed / total
    if direction == "buy":
        if ratio == 1.0:
            return "Strong Buy"
        elif ratio >= 0.75:
            return "Buy"
        elif ratio >= 0.5:
            return "Watch"
        else:
            return "Hold"
    else:
        if ratio == 1.0:
            return "Strong Sell"
        elif ratio >= 0.75:
            return "Sell"
        elif ratio >= 0.5:
            return "Watch"
        else:
            return "Hold"


def get_technical_signal(ticker: str) -> dict:
    df = fetch_ohlcv(ticker)
    df = add_all_indicators(df)
    div = detect_rsi_divergence(df)

    buy_passed, buy_failed = _evaluate_buy_conditions(df, div)
    sell_passed, sell_failed = _evaluate_sell_conditions(df, div)

    buy_score = len(buy_passed) / (len(buy_passed) + len(buy_failed))
    sell_score = len(sell_passed) / (len(sell_passed) + len(sell_failed))

    if sell_score > buy_score and sell_score >= 0.5:
        direction = "sell"
        tier = _tier_from_counts(len(sell_passed), len(sell_passed) + len(sell_failed), "sell")
        reasons = sell_passed
        misses = sell_failed
    else:
        direction = "buy"
        tier = _tier_from_counts(len(buy_passed), len(buy_passed) + len(buy_failed), "buy")
        reasons = buy_passed
        misses = buy_failed

    row = df.iloc[-1]
    return {
        "ticker": ticker,
        "direction": direction,
        "tier": tier,
        "buy_score": round(buy_score, 2),
        "sell_score": round(sell_score, 2),
        "reasons": reasons,
        "misses": misses,
        "price": round(float(row["Close"]), 2),
        "rsi": round(float(row["rsi"]), 1) if pd.notna(row["rsi"]) else None,
        "atr": round(float(row["atr"]), 2) if pd.notna(row["atr"]) else None,
    }
