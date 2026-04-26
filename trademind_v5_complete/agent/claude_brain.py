"""
agent/claude_brain.py

THIS is what makes the agent actually use Claude's intelligence
instead of just rule-based signal logic.

Every trade decision is made by the Claude API after analyzing:
- Current OHLCV price data + indicators
- Recent news headlines
- Sector context
- Current portfolio state

Claude returns a structured JSON decision.
Your script executes it via Kite API.

Requirements:
    pip install anthropic
    Set ANTHROPIC_API_KEY in your .env file
    Get key from: https://console.anthropic.com/
"""

import json
import logging
from datetime import datetime

import anthropic
import pandas as pd

from config.settings import TOTAL_CAPITAL
from data.fetcher import fetch_ohlcv, fetch_news_headlines, filter_headlines_for_ticker
from signals.technical import compute_indicators

logger = logging.getLogger(__name__)

# Initialize Anthropic client (reads ANTHROPIC_API_KEY from environment)
client = anthropic.Anthropic()


# ── System prompt — this defines how Claude thinks about trades ───────────────

TRADING_SYSTEM_PROMPT = """You are TradeMind — an expert quantitative trader focused exclusively on NSE/BSE Indian markets.

Your job: analyze market data for a stock and return a precise trade decision.

You think like a disciplined swing trader:
- You ONLY buy stocks in uptrends with volume confirmation
- You NEVER chase overbought stocks (RSI > 65)
- You ALWAYS define stop loss before entry
- You prefer 1:2 minimum reward-to-risk
- You consider news sentiment as a supporting signal, never the primary one
- You are conservative — HOLD is always a valid answer
- You factor in current portfolio exposure before recommending new positions

You must respond with ONLY a valid JSON object — no explanation, no markdown, no extra text.

JSON format:
{
  "decision": "BUY" | "HOLD" | "AVOID",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "score": 0.0 to 1.0,
  "entry_zone": {"low": float, "high": float},
  "stop_loss": float,
  "target_1": float,
  "target_2": float,
  "hold_days": integer (expected swing trade duration),
  "reasoning": {
    "trend": "one line on price trend",
    "momentum": "one line on RSI/MACD",
    "volume": "one line on volume",
    "sentiment": "one line on news",
    "risk": "one line on key risk"
  },
  "red_flags": ["list of concerns, empty if none"]
}

Rules for decision values:
- BUY: score >= 0.65, trend is up, RSI 35-60, volume confirming, no major red flags
- HOLD: score 0.40-0.64, mixed signals, wait for better entry
- AVOID: score < 0.40, downtrend, overbought, bad news, or red flags present
"""


def prepare_market_context(ticker: str, headlines: list = None) -> str:
    """
    Prepare a comprehensive market data summary to send to Claude.
    This is what Claude sees when making a decision.
    """
    # ── Fetch and compute indicators ─────────────────────────────────────────
    df = fetch_ohlcv(ticker, period="3mo")
    if df.empty:
        return f"No data available for {ticker}"

    df = compute_indicators(df)
    if df.empty:
        return f"Indicator computation failed for {ticker}"

    latest  = df.iloc[-1]
    prev    = df.iloc[-2]
    week_ago = df.iloc[-6] if len(df) >= 6 else df.iloc[0]
    month_ago = df.iloc[-22] if len(df) >= 22 else df.iloc[0]

    price        = float(latest["Close"])
    prev_close   = float(prev["Close"])
    week_price   = float(week_ago["Close"])
    month_price  = float(month_ago["Close"])
    high_52w     = float(df["High"].max())
    low_52w      = float(df["Low"].min())
    avg_vol_20   = float(df["Volume"].tail(20).mean())
    today_vol    = float(latest["Volume"])

    ema20  = float(latest.get("EMA_20", price))
    ema50  = float(latest.get("EMA_50", price))
    ema200 = float(latest.get("EMA_200", price))
    rsi    = float(latest.get("RSI", 50))
    macd   = float(latest.get("MACD", 0))
    macd_s = float(latest.get("MACD_signal", 0))
    bb_up  = float(latest.get("BB_upper", price * 1.05))
    bb_lo  = float(latest.get("BB_lower", price * 0.95))

    # ── Last 5 candles summary ───────────────────────────────────────────────
    last5 = df.tail(5)[["Close", "Volume"]].copy()
    last5["Close"] = last5["Close"].round(2)
    candles_str = "\n".join(
        f"  {df.index[-(5-i)].strftime('%d %b')}: ₹{float(row['Close']):.2f}"
        for i, (_, row) in enumerate(last5.iterrows())
    )

    # ── News context ─────────────────────────────────────────────────────────
    relevant_news = []
    if headlines:
        relevant_news = filter_headlines_for_ticker(headlines, ticker)[:5]
    news_str = "\n".join(f"  - {h['title']}" for h in relevant_news) if relevant_news else "  No recent news found"

    # ── Format context ────────────────────────────────────────────────────────
    context = f"""
STOCK ANALYSIS REQUEST: {ticker} | {datetime.now().strftime('%d %b %Y %H:%M IST')}

PRICE ACTION:
  Current price    : ₹{price:.2f}
  Previous close   : ₹{prev_close:.2f} ({((price-prev_close)/prev_close*100):+.2f}% today)
  1 week ago       : ₹{week_price:.2f} ({((price-week_price)/week_price*100):+.2f}%)
  1 month ago      : ₹{month_price:.2f} ({((price-month_price)/month_price*100):+.2f}%)
  52-week high     : ₹{high_52w:.2f} ({((price-high_52w)/high_52w*100):.1f}% from high)
  52-week low      : ₹{low_52w:.2f} ({((price-low_52w)/low_52w*100):+.1f}% from low)

LAST 5 CANDLES:
{candles_str}

TECHNICAL INDICATORS:
  EMA 20           : ₹{ema20:.2f} | Price {'ABOVE' if price > ema20 else 'BELOW'} EMA20
  EMA 50           : ₹{ema50:.2f} | Price {'ABOVE' if price > ema50 else 'BELOW'} EMA50
  EMA 200          : ₹{ema200:.2f} | Price {'ABOVE' if price > ema200 else 'BELOW'} EMA200
  RSI (14)         : {rsi:.1f} ({'OVERSOLD' if rsi < 35 else 'OVERBOUGHT' if rsi > 65 else 'NEUTRAL'})
  MACD             : {macd:.3f} | Signal: {macd_s:.3f} | {'BULLISH' if macd > macd_s else 'BEARISH'} crossover
  Bollinger Upper  : ₹{bb_up:.2f}
  Bollinger Lower  : ₹{bb_lo:.2f}
  Price in BB      : {((price - bb_lo) / (bb_up - bb_lo) * 100):.0f}% of range

VOLUME:
  Today's volume   : {today_vol:,.0f}
  20-day avg vol   : {avg_vol_20:,.0f}
  Volume ratio     : {(today_vol/avg_vol_20):.2f}x ({'HIGH' if today_vol > avg_vol_20 * 1.5 else 'NORMAL' if today_vol > avg_vol_20 * 0.8 else 'LOW'})

RECENT NEWS:
{news_str}

PORTFOLIO CONTEXT:
  Total capital    : ₹{TOTAL_CAPITAL:,.0f}
  Risk per trade   : 5% = ₹{TOTAL_CAPITAL * 0.05:,.0f} max loss allowed
  Max SL distance  : 7% below entry

Based on all the above, provide your trade decision as JSON.
"""
    return context.strip()


async def get_claude_decision(ticker: str, headlines: list = None) -> dict:
    """
    Send market data to Claude API and get a structured trade decision back.

    Returns:
        dict with decision, confidence, entry_zone, stop_loss, targets, reasoning
    """
    logger.info(f"Sending {ticker} to Claude for analysis...")

    context = prepare_market_context(ticker, headlines)
    if "No data" in context or "failed" in context:
        return {"decision": "HOLD", "confidence": "LOW", "score": 0.5,
                "reasoning": {"trend": "Data unavailable"}, "red_flags": ["No data"]}

    try:
        message = client.messages.create(
            model      = "claude-opus-4-5",   # use Opus for best trading analysis
            max_tokens = 800,
            system     = TRADING_SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": context}]
        )

        raw = message.content[0].text.strip()

        # Clean any accidental markdown
        raw = raw.replace("```json", "").replace("```", "").strip()

        decision = json.loads(raw)
        logger.info(
            f"Claude decision for {ticker}: {decision['decision']} | "
            f"Score: {decision.get('score', '?')} | "
            f"Confidence: {decision.get('confidence', '?')}"
        )
        return decision

    except json.JSONDecodeError as e:
        logger.error(f"Claude returned invalid JSON for {ticker}: {e}")
        logger.debug(f"Raw response: {raw}")
        return {"decision": "HOLD", "confidence": "LOW", "score": 0.5,
                "reasoning": {"trend": "JSON parse error"}, "red_flags": ["Parse error"]}

    except Exception as e:
        logger.error(f"Claude API call failed for {ticker}: {e}")
        return {"decision": "HOLD", "confidence": "LOW", "score": 0.5,
                "reasoning": {"trend": "API error"}, "red_flags": [str(e)]}


def get_claude_decision_sync(ticker: str, headlines: list = None) -> dict:
    """
    Synchronous version — use this in auto_executor.py.
    Calls the Anthropic API and returns Claude's decision.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context — use thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, get_claude_decision(ticker, headlines))
                return future.result(timeout=30)
        else:
            return loop.run_until_complete(get_claude_decision(ticker, headlines))
    except Exception:
        return asyncio.run(get_claude_decision(ticker, headlines))


def claude_portfolio_review(open_positions: dict, daily_pnl: float) -> dict:
    """
    Ask Claude to review the current portfolio and suggest any exits
    or position adjustments.

    Call this once a day — mid-session or before close.
    """
    if not open_positions:
        return {"action": "NONE", "notes": "No open positions to review"}

    positions_str = "\n".join([
        f"  {ticker}: entry ₹{pos['entry_price']:.2f} | "
        f"SL ₹{pos['sl_price']:.2f} | T1 ₹{pos['target_1']:.2f} | "
        f"qty {pos['qty']} | opened {pos.get('opened_at','?')[:10]}"
        for ticker, pos in open_positions.items()
    ])

    prompt = f"""
PORTFOLIO REVIEW REQUEST — {datetime.now().strftime('%d %b %Y %H:%M IST')}

OPEN POSITIONS:
{positions_str}

TODAY'S P&L SO FAR: ₹{daily_pnl:+,.0f}
TOTAL CAPITAL: ₹{TOTAL_CAPITAL:,.0f}

For each open position, should I:
1. HOLD — let it run, SL and target unchanged
2. TRAIL_SL — move stop loss up to protect profits
3. EXIT — close now (with brief reason)

Respond with ONLY JSON:
{{
  "positions": {{
    "TICKER": {{
      "action": "HOLD" | "TRAIL_SL" | "EXIT",
      "new_sl": float | null,
      "reason": "one line"
    }}
  }},
  "overall_notes": "brief portfolio-level observation"
}}
"""

    try:
        message = client.messages.create(
            model      = "claude-opus-4-5",
            max_tokens = 500,
            system     = TRADING_SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip().replace("```json","").replace("```","").strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Portfolio review failed: {e}")
        return {"action": "ERROR", "notes": str(e)}
