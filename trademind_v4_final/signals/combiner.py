"""
signals/combiner.py
Combines technical, sentiment, and volume signals into a final score.
This is the brain's input — a single number that drives the trade decision.
"""

from config.settings import (
    WEIGHT_TECHNICAL, WEIGHT_SENTIMENT, WEIGHT_VOLUME,
    BUY_SCORE_THRESHOLD, SELL_SCORE_THRESHOLD
)


def combine_signals(technical: dict, sentiment: dict, volume_ratio: float) -> dict:
    """
    Combine all signals into a final weighted score.

    Args:
        technical:    output from signals/technical.py → score_technical()
        sentiment:    output from signals/sentiment.py → score_sentiment()
        volume_ratio: float — current volume / 20-day avg volume

    Returns:
        {
            "final_score":  float (0.0–1.0),
            "signal":       "BUY" | "SELL" | "HOLD",
            "confidence":   "HIGH" | "MEDIUM" | "LOW",
            "breakdown":    dict of component scores,
            "reasons":      list[str]
        }
    """
    tech_score      = technical.get("score", 0.5)
    sentiment_score = sentiment.get("score", 0.5)

    # Normalize volume to 0–1 (clamp at 3x average)
    vol_score = min(volume_ratio / 3.0, 1.0)

    # Weighted combination
    final_score = (
        tech_score      * WEIGHT_TECHNICAL  +
        sentiment_score * WEIGHT_SENTIMENT  +
        vol_score       * WEIGHT_VOLUME
    )
    final_score = round(final_score, 3)

    # Signal classification
    if final_score >= BUY_SCORE_THRESHOLD:
        signal = "BUY"
    elif final_score <= SELL_SCORE_THRESHOLD:
        signal = "SELL"
    else:
        signal = "HOLD"

    # Confidence: how far score is from the threshold
    distance = abs(final_score - 0.5)
    if distance >= 0.20:
        confidence = "HIGH"
    elif distance >= 0.12:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Compile all reasons
    reasons = technical.get("reasons", [])
    if sentiment.get("reason"):
        reasons.append(sentiment["reason"])

    return {
        "final_score": final_score,
        "signal":      signal,
        "confidence":  confidence,
        "breakdown": {
            "technical_score":  round(tech_score, 3),
            "sentiment_score":  round(sentiment_score, 3),
            "volume_score":     round(vol_score, 3),
            "weights":          {
                "technical":  WEIGHT_TECHNICAL,
                "sentiment":  WEIGHT_SENTIMENT,
                "volume":     WEIGHT_VOLUME,
            }
        },
        "reasons": reasons,
    }
