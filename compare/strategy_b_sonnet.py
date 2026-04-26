"""
compare/strategy_b_sonnet.py  (v2 — with fallback)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRATEGY B — Claude Sonnet 4.6  +  Automatic Fallback

When Sonnet API fails:
  → Saves the original request to retry queue
  → Notifies via Telegram + desktop
  → Falls back to rule-based automatically
  → Retries when API recovers

Run standalone:
    python -m compare.strategy_b_sonnet
"""

import json, time, schedule
import yfinance as yf
from datetime import datetime
import anthropic

from config.settings import TOTAL_CAPITAL
from config.watchlist import WATCHLIST
from data.fetcher import fetch_ohlcv, fetch_news_headlines, filter_headlines_for_ticker
from signals.technical import compute_indicators, score_technical
from signals.sentiment import score_sentiment_keywords
from signals.combiner import combine_signals
from compare.shared_logger import ensure_log, append_signal, load_all, save_all, get_logger
from compare.api_fallback import (
    handle_api_failure, retry_pending_requests, check_api_health
)

STRATEGY_ID  = "B"
MODEL        = "claude-sonnet-4-20250514"
PRICE_INPUT  = 3.0
PRICE_OUTPUT = 15.0
logger = get_logger("StrategyB-Sonnet")
client = anthropic.Anthropic()

SYSTEM = """You are a disciplined NSE/BSE swing trading signal engine.
Return ONLY a JSON object:
{
  "signal": "BUY"|"HOLD"|"AVOID",
  "score": 0.0-1.0,
  "confidence": "HIGH"|"MEDIUM"|"LOW",
  "reasoning": "one sentence",
  "stop_loss": float,
  "target_1": float,
  "red_flags": []
}
No markdown. No preamble. JSON only."""


class StrategyB:
    def __init__(self):
        ensure_log(STRATEGY_ID)
        self.daily_cost_usd = 0.0
        self._api_healthy   = True
        logger.info(f"Strategy B (Sonnet) ready | {MODEL}")

    def run_scan(self):
        logger.info(f"=== SONNET SCAN {datetime.now().strftime('%d %b %Y %H:%M')} ===")
        health = check_api_health(MODEL)
        self._api_healthy = health["healthy"]
        if not self._api_healthy:
            logger.warning(f"Sonnet unhealthy: {health['error']} — fallback mode")
        else:
            logger.info(f"Sonnet OK | Latency: {health['latency_ms']}ms")

        headlines = fetch_news_headlines(max_articles=100)
        scan_cost = 0.0

        for ticker in WATCHLIST:
            result = self._analyse(ticker, headlines)
            if result:
                append_signal(STRATEGY_ID, result)
                scan_cost += float(result.get("api_cost_usd", 0))
                fb = " [FALLBACK]" if result.get("fallback_used") else ""
                logger.info(f"  {ticker:20s} | {result['signal']:5s} | Score:{result['score']:.3f} | ${result['api_cost_usd']:.4f}{fb}")
            time.sleep(1.5)

        self.daily_cost_usd += scan_cost
        logger.info(f"Sonnet scan done | Cost: ${scan_cost:.4f} (~₹{scan_cost*83:.1f})")

        if self._api_healthy:
            retry_pending_requests(sonnet_fn=self._retry_call, max_retries=3)

    def _analyse(self, ticker: str, headlines: list) -> dict | None:
        context = self._build_context(ticker, headlines)
        if not context:
            return None
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=300, system=SYSTEM,
                messages=[{"role": "user", "content": context}]
            )
            it, ot = resp.usage.input_tokens, resp.usage.output_tokens
            cost   = (it/1_000_000)*PRICE_INPUT + (ot/1_000_000)*PRICE_OUTPUT
            raw    = resp.content[0].text.strip().replace("```json","").replace("```","")
            d      = json.loads(raw)
            price  = self._live_price(ticker)
            sl     = d.get("stop_loss") or (round(price*0.93,2) if price else 0)
            t1     = d.get("target_1")  or (round(price+(price-sl)*2,2) if price else 0)
            return {
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "ticker": ticker,
                "signal": d.get("signal","HOLD"), "score": round(float(d.get("score",0.5)),3),
                "confidence": d.get("confidence","LOW"), "entry_price": price or "",
                "sl_price": sl, "target_1": t1, "result": "OPEN", "correct": "PENDING",
                "detail": d.get("reasoning","")[:150], "api_cost_usd": round(cost,6),
                "fallback_used": False,
            }
        except Exception as e:
            return handle_api_failure(
                error=e, strategy_id=STRATEGY_ID, ticker=ticker,
                original_context=context, fallback_fn=self._rule_fallback,
                headlines=headlines,
            )

    def _retry_call(self, ticker: str, context: str) -> dict | None:
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=300, system=SYSTEM,
                messages=[{"role":"user","content":context}]
            )
            raw = resp.content[0].text.strip().replace("```json","").replace("```","")
            d   = json.loads(raw)
            price = self._live_price(ticker)
            sl  = d.get("stop_loss") or (round(price*0.93,2) if price else 0)
            t1  = d.get("target_1") or (round(price+(price-sl)*2,2) if price else 0)
            result = {
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "ticker": ticker,
                "signal": d.get("signal","HOLD"), "score": round(float(d.get("score",0.5)),3),
                "confidence": d.get("confidence","LOW"), "entry_price": price or "",
                "sl_price": sl, "target_1": t1, "result": "OPEN", "correct": "PENDING",
                "detail": "[RETRY] "+d.get("reasoning","")[:140],
                "api_cost_usd": 0.001, "fallback_used": False,
            }
            append_signal(STRATEGY_ID, result)
            return result
        except Exception as e:
            logger.error(f"Retry failed {ticker}: {e}")
            return None

    def _rule_fallback(self, ticker: str, headlines: list) -> dict | None:
        try:
            df = fetch_ohlcv(ticker, period="6mo")
            if df.empty or len(df) < 60: return None
            df = compute_indicators(df)
            latest = df.iloc[-1]
            price  = float(latest["Close"])
            vol_r  = float(latest.get("Volume_ratio",1.0))
            sl, t1 = round(price*0.93,2), round(price+(price*0.07)*2,2)
            tech   = score_technical(df)
            sent   = score_sentiment_keywords(filter_headlines_for_ticker(headlines,ticker))
            comb   = combine_signals(tech, sent, vol_r)
            return {
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "ticker": ticker,
                "signal": comb["signal"], "score": round(comb["final_score"],3),
                "confidence": comb["confidence"], "entry_price": price,
                "sl_price": sl, "target_1": t1, "result": "OPEN", "correct": "PENDING",
                "detail": f"[FALLBACK-RULE] RSI:{tech.get('rsi','?')} Trend:{tech.get('trend','?')}",
                "api_cost_usd": 0.0, "fallback_used": True,
            }
        except Exception as e:
            logger.error(f"Rule fallback failed {ticker}: {e}")
            return None

    def _build_context(self, ticker: str, headlines: list) -> str | None:
        df = fetch_ohlcv(ticker, period="3mo")
        if df.empty or len(df) < 30: return None
        df = compute_indicators(df)
        l  = df.iloc[-1]
        p  = float(l["Close"])
        news = " | ".join(h["title"] for h in filter_headlines_for_ticker(headlines,ticker)[:3]) or "None"
        return (
            f"Stock:{ticker} Price:₹{p:.2f}\n"
            f"EMA20:₹{float(l.get('EMA_20',p)):.2f}({'above' if p>float(l.get('EMA_20',p)) else 'below'}) "
            f"EMA50:₹{float(l.get('EMA_50',p)):.2f}({'above' if p>float(l.get('EMA_50',p)) else 'below'})\n"
            f"RSI:{float(l.get('RSI',50)):.1f} MACD:{float(l.get('MACD',0)):.3f} "
            f"Vol:{float(l.get('Volume_ratio',1)):.1f}x SL-budget:₹{p*0.07:.2f}\nNews:{news}"
        )

    def _live_price(self, ticker: str) -> float | None:
        try:
            df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
            return float(df["Close"].iloc[-1]) if not df.empty else None
        except Exception: return None

    def update_outcome(self, ticker, exit_price, exit_date, signal_date):
        rows = load_all(STRATEGY_ID)
        for row in rows:
            if row["ticker"]==ticker and signal_date in row["date"] and row["result"]=="OPEN":
                entry = float(row["entry_price"]) if row["entry_price"] else 0
                t1    = float(row["target_1"])    if row["target_1"]    else 0
                sl    = float(row["sl_price"])    if row["sl_price"]    else 0
                pnl   = round(exit_price - entry, 2)
                row.update({"exit_price":exit_price,"exit_date":exit_date,"pnl":pnl,
                    "result":"WIN" if pnl>0 else "LOSS",
                    "correct":"YES" if ((row["signal"]=="BUY" and exit_price>=t1) or
                                        (row["signal"]!="BUY" and exit_price<=sl)) else "NO"})
                break
        save_all(STRATEGY_ID, rows)

    def run_forever(self):
        schedule.every().day.at("09:40").do(self.run_scan)
        logger.info("Strategy B scheduled 09:40 daily")
        self.run_scan()
        while True:
            schedule.run_pending()
            time.sleep(30)


if __name__ == "__main__":
    StrategyB().run_forever()
