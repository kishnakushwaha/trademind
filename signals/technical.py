"""
signals/technical.py
Computes technical indicators and returns a normalized score 0.0–1.0.
Score > 0.65 = bullish. Score < 0.35 = bearish. In between = neutral.
"""

import pandas as pd
import numpy as np
import logging

from config.settings import (
    EMA_FAST, EMA_SLOW, RSI_OVERSOLD, RSI_OVERBOUGHT,
    RSI_NEUTRAL_LOW, RSI_NEUTRAL_HIGH, VOLUME_MULTIPLIER
)

logger = logging.getLogger(__name__)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all technical indicators to OHLCV DataFrame.
    Uses the 'ta' library for reliable calculation.
    """
    if df.empty or len(df) < 60:
        logger.warning("Insufficient data for indicator calculation (need 60+ rows)")
        return df

    df = df.copy()

    # Trend — EMA
    df[f"EMA_{EMA_FAST}"]  = df["Close"].ewm(span=EMA_FAST, adjust=False).mean()
    df[f"EMA_{EMA_SLOW}"]  = df["Close"].ewm(span=EMA_SLOW, adjust=False).mean()
    df["EMA_200"]           = df["Close"].ewm(span=200, adjust=False).mean()

    # Momentum — RSI
    try:
        import ta as ta_lib
        df["RSI"] = ta_lib.momentum.RSIIndicator(df["Close"], window=14).rsi()

        # MACD
        macd_ind = ta_lib.trend.MACD(df["Close"], window_slow=26, window_fast=12, window_sign=9)
        df["MACD"]        = macd_ind.macd()
        df["MACD_signal"] = macd_ind.macd_signal()
        df["MACD_hist"]   = macd_ind.macd_diff()

        # Bollinger Bands
        bb_ind = ta_lib.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
        df["BB_upper"] = bb_ind.bollinger_hband()
        df["BB_lower"] = bb_ind.bollinger_lband()
        df["BB_mid"]   = bb_ind.bollinger_mavg()
    except ImportError:
        # Fallback: manual RSI calculation if 'ta' not installed
        logger.warning("'ta' library not installed, using manual RSI calculation")
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

    # Volume
    df["Volume_MA20"]      = df["Volume"].rolling(window=20).mean()
    df["Volume_ratio"]     = df["Volume"] / df["Volume_MA20"]

    # Support / Resistance (simple swing high/low over 20 bars)
    df["Swing_high"]       = df["High"].rolling(window=20).max()
    df["Swing_low"]        = df["Low"].rolling(window=20).min()

    df.dropna(inplace=True)
    return df



def score_technical(df: pd.DataFrame) -> dict:
    """
    Compute a normalized technical score from the latest candle.

    Returns:
        {
            "score": float (0.0–1.0),
            "rsi": float,
            "trend": "up" | "down" | "sideways",
            "macd_cross": bool,
            "volume_ok": bool,
            "reasons": list[str]   ← human-readable explanation
        }
    """
    if df.empty:
        return {"score": 0.5, "reasons": ["No data"]}

    latest = df.iloc[-1]
    prev   = df.iloc[-2] if len(df) > 1 else latest
    score_components = []
    reasons = []

    # ── 1. Trend: price vs EMA ────────────────────────────────────────────────
    price = latest["Close"]
    ema_fast = latest.get(f"EMA_{EMA_FAST}", price)
    ema_slow = latest.get(f"EMA_{EMA_SLOW}", price)
    ema_200  = latest.get("EMA_200", price)

    if price > ema_fast > ema_slow:
        trend = "up"
        score_components.append(1.0)
        reasons.append(f"Price above EMA{EMA_FAST} and EMA{EMA_SLOW} — uptrend confirmed")
    elif price < ema_fast < ema_slow:
        trend = "down"
        score_components.append(0.0)
        reasons.append(f"Price below EMA{EMA_FAST} and EMA{EMA_SLOW} — downtrend")
    else:
        trend = "sideways"
        score_components.append(0.5)
        reasons.append("Mixed EMA signals — sideways/consolidation")

    # Bonus: above 200 EMA
    if price > ema_200:
        score_components.append(0.7)
        reasons.append("Price above 200 EMA — long-term uptrend intact")
    else:
        score_components.append(0.3)
        reasons.append("Price below 200 EMA — long-term trend weak")

    # ── 2. RSI ────────────────────────────────────────────────────────────────
    rsi = latest.get("RSI", 50)
    if RSI_NEUTRAL_LOW <= rsi <= RSI_NEUTRAL_HIGH:
        score_components.append(0.75)
        reasons.append(f"RSI {rsi:.1f} — ideal zone for entry (not overbought)")
    elif rsi < RSI_OVERSOLD:
        score_components.append(0.85)
        reasons.append(f"RSI {rsi:.1f} — oversold, potential bounce")
    elif rsi > RSI_OVERBOUGHT:
        score_components.append(0.15)
        reasons.append(f"RSI {rsi:.1f} — overbought, risky entry")
    else:
        score_components.append(0.5)
        reasons.append(f"RSI {rsi:.1f} — neutral zone")

    # ── 3. MACD ───────────────────────────────────────────────────────────────
    macd_val  = latest.get("MACD", 0)
    macd_sig  = latest.get("MACD_signal", 0)
    macd_hist = latest.get("MACD_hist", 0)
    prev_hist = prev.get("MACD_hist", 0)

    macd_cross = False
    if macd_val > macd_sig and prev.get("MACD", 0) <= prev.get("MACD_signal", 0):
        macd_cross = True
        score_components.append(0.85)
        reasons.append("MACD bullish crossover just happened — strong signal")
    elif macd_val > macd_sig:
        score_components.append(0.65)
        reasons.append("MACD above signal line — bullish momentum")
    elif macd_hist > prev_hist:
        score_components.append(0.55)
        reasons.append("MACD histogram increasing — momentum building")
    else:
        score_components.append(0.25)
        reasons.append("MACD bearish — avoid entry")

    # ── 4. Volume ─────────────────────────────────────────────────────────────
    vol_ratio = latest.get("Volume_ratio", 1.0)
    volume_ok = vol_ratio >= VOLUME_MULTIPLIER

    if volume_ok:
        score_components.append(0.8)
        reasons.append(f"Volume {vol_ratio:.1f}x average — strong confirmation")
    elif vol_ratio >= 1.0:
        score_components.append(0.5)
        reasons.append(f"Volume {vol_ratio:.1f}x average — average, acceptable")
    else:
        score_components.append(0.2)
        reasons.append(f"Volume {vol_ratio:.1f}x average — weak, be cautious")

    # ── 5. Bollinger Band position ────────────────────────────────────────────
    bb_upper = latest.get("BB_upper", price * 1.05)
    bb_lower = latest.get("BB_lower", price * 0.95)
    bb_mid   = latest.get("BB_mid", price)
    bb_range = bb_upper - bb_lower

    if bb_range > 0:
        bb_position = (price - bb_lower) / bb_range  # 0=at lower, 1=at upper
        if 0.2 <= bb_position <= 0.6:
            score_components.append(0.7)
            reasons.append(f"Price in lower-mid BB zone — good entry region")
        elif bb_position < 0.2:
            score_components.append(0.8)
            reasons.append("Price near BB lower band — potential bounce")
        else:
            score_components.append(0.2)
            reasons.append("Price near BB upper band — risky, may pull back")

    final_score = float(np.mean(score_components))

    return {
        "score":       round(final_score, 3),
        "rsi":         round(float(rsi), 2),
        "trend":       trend,
        "macd_cross":  macd_cross,
        "volume_ok":   volume_ok,
        "vol_ratio":   round(float(vol_ratio), 2),
        "reasons":     reasons,
    }
