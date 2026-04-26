"""
signals/sentiment.py
Analyses news headlines for financial sentiment using a pre-trained NLP model.
Uses FinBERT (ProsusAI/finbert) — specifically trained on financial text.

First run: model downloads (~400MB). After that it's cached locally.
"""

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Lazy import — only load model when first needed (saves memory at startup)
_pipeline = None


def _load_model():
    """Load FinBERT sentiment pipeline (cached after first load)."""
    global _pipeline
    if _pipeline is None:
        try:
            from transformers import pipeline
            logger.info("Loading FinBERT model (first run: downloading ~400MB)...")
            _pipeline = pipeline(
                "text-classification",
                model="ProsusAI/finbert",
                return_all_scores=True,
                device=-1      # -1 = CPU; change to 0 for GPU if available
            )
            logger.info("FinBERT loaded successfully")
        except ImportError:
            logger.error("transformers not installed. Run: pip install transformers torch")
            _pipeline = None
    return _pipeline


def analyse_headline(text: str) -> dict:
    """
    Analyse a single headline for financial sentiment.

    Returns:
        {
            "label":    "positive" | "negative" | "neutral",
            "score":    float (0.0–1.0 confidence),
            "numeric":  float (1.0=positive, 0.5=neutral, 0.0=negative)
        }
    """
    pipe = _load_model()
    if pipe is None:
        return {"label": "neutral", "score": 0.5, "numeric": 0.5}

    try:
        # Truncate to 512 tokens max (FinBERT limit)
        text = text[:500]
        results = pipe(text)[0]
        # results = [{"label": "positive", "score": 0.9}, ...]
        best = max(results, key=lambda x: x["score"])
        numeric_map = {"positive": 1.0, "neutral": 0.5, "negative": 0.0}
        return {
            "label":   best["label"],
            "score":   round(best["score"], 3),
            "numeric": numeric_map.get(best["label"], 0.5),
        }
    except Exception as e:
        logger.error(f"Sentiment analysis failed: {e}")
        return {"label": "neutral", "score": 0.5, "numeric": 0.5}


def score_sentiment(headlines: list[dict]) -> dict:
    """
    Aggregate sentiment across multiple headlines for a stock.

    Args:
        headlines: list of {"title": str, ...} dicts from fetcher.py

    Returns:
        {
            "score":          float (0.0–1.0, weighted average),
            "positive_count": int,
            "negative_count": int,
            "neutral_count":  int,
            "top_headlines":  list[str],
            "reason":         str
        }
    """
    if not headlines:
        return {
            "score":          0.5,
            "positive_count": 0,
            "negative_count": 0,
            "neutral_count":  0,
            "top_headlines":  [],
            "reason":         "No relevant headlines found — neutral assumed"
        }

    scores = []
    pos, neg, neu = 0, 0, 0

    for h in headlines[:15]:   # cap at 15 headlines per stock
        result = analyse_headline(h["title"])
        scores.append(result["numeric"])
        if result["label"] == "positive":
            pos += 1
        elif result["label"] == "negative":
            neg += 1
        else:
            neu += 1

    avg_score = sum(scores) / len(scores) if scores else 0.5

    # Determine summary reason
    if avg_score >= 0.65:
        reason = f"Positive news sentiment ({pos} positive, {neg} negative headlines)"
    elif avg_score <= 0.35:
        reason = f"Negative news sentiment ({neg} negative headlines — caution)"
    else:
        reason = f"Neutral/mixed news sentiment ({pos}P {neg}N {neu}neutral)"

    return {
        "score":          round(avg_score, 3),
        "positive_count": pos,
        "negative_count": neg,
        "neutral_count":  neu,
        "top_headlines":  [h["title"] for h in headlines[:3]],
        "reason":         reason,
    }


# ── Fallback: keyword-based sentiment (no model required) ─────────────────────
# Use this if transformers install fails on low-RAM machines

BULLISH_KEYWORDS = [
    "profit", "growth", "revenue", "beats", "strong", "record", "upgrade",
    "buy", "target", "outperform", "surge", "rally", "gain", "positive",
    "expansion", "order", "win", "partnership", "deal"
]
BEARISH_KEYWORDS = [
    "loss", "decline", "fraud", "penalty", "fine", "downgrade", "sell",
    "underperform", "fall", "drop", "crash", "debt", "weak", "cut",
    "layoff", "scam", "probe", "investigation", "miss"
]


def score_sentiment_keywords(headlines: list[dict]) -> dict:
    """
    Fallback keyword-based sentiment scorer. No model needed.
    Less accurate than FinBERT but works without GPU/heavy install.
    """
    if not headlines:
        return {"score": 0.5, "reason": "No headlines — neutral"}

    total_score = 0
    pos, neg = 0, 0

    for h in headlines[:15]:
        title = h["title"].lower()
        bull_hits = sum(1 for kw in BULLISH_KEYWORDS if kw in title)
        bear_hits = sum(1 for kw in BEARISH_KEYWORDS if kw in title)

        if bull_hits > bear_hits:
            total_score += 1.0; pos += 1
        elif bear_hits > bull_hits:
            total_score += 0.0; neg += 1
        else:
            total_score += 0.5

    avg = total_score / len(headlines[:15])
    reason = f"Keyword sentiment: {pos} bullish, {neg} bearish signals"
    return {"score": round(avg, 3), "reason": reason,
            "positive_count": pos, "negative_count": neg, "neutral_count": 0}
