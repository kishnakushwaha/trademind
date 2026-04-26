"""
compare/strategy_a_rulebased.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRATEGY A — Rule-Based Signal Engine
Cost: ₹0 (no API calls)

Logic:
  - EMA 20/50/200 for trend
  - RSI 14 for momentum
  - MACD for crossover
  - Volume ratio for confirmation
  - Keyword sentiment on headlines

Run standalone:
    python -m compare.strategy_a_rulebased
"""

import time
import schedule
import yfinance as yf
from datetime import datetime, date

from config.settings import TOTAL_CAPITAL, BUY_SCORE_THRESHOLD
from config.watchlist import WATCHLIST
from data.fetcher import fetch_ohlcv, fetch_news_headlines, filter_headlines_for_ticker
from signals.technical import compute_indicators, score_technical
from signals.sentiment import score_sentiment_keywords
from signals.combiner import combine_signals
from compare.shared_logger import ensure_log, append_signal, load_all, save_all, get_logger

STRATEGY_ID = "A"
logger = get_logger("StrategyA")


class StrategyA:
    """
    Fully self-contained rule-based trading signal engine.
    Writes only to logs/strategy_a_rulebased.csv
    Never imports or touches Strategy B or C.
    """

    def __init__(self):
        ensure_log(STRATEGY_ID)
        self.today_signals: list[dict] = []
        logger.info("Strategy A (Rule-Based) initialized")

    def run_scan(self):
        """Scan all watchlist stocks and log signals."""
        logger.info(f"=== STRATEGY A SCAN — {datetime.now().strftime('%d %b %Y %H:%M')} ===")
        headlines = fetch_news_headlines(max_articles=100)
        self.today_signals = []

        for ticker in WATCHLIST:
            result = self._analyse(ticker, headlines)
            if result:
                self.today_signals.append(result)
                append_signal(STRATEGY_ID, result)
                logger.info(
                    f"  {ticker:20s} | {result['signal']:5s} | "
                    f"Score: {result['score']:.3f} | RSI: {result.get('detail','')[:30]}"
                )
            time.sleep(0.3)

        buy_count = sum(1 for r in self.today_signals if r["signal"] == "BUY")
        logger.info(f"Strategy A done: {buy_count} BUY signals from {len(self.today_signals)} stocks")

    def _analyse(self, ticker: str, headlines: list) -> dict | None:
        try:
            df = fetch_ohlcv(ticker, period="6mo")
            if df.empty or len(df) < 60:
                return None

            df        = compute_indicators(df)
            latest    = df.iloc[-1]
            price     = float(latest["Close"])
            vol_ratio = float(latest.get("Volume_ratio", 1.0))
            sl        = round(price * 0.93, 2)
            t1        = round(price + (price - sl) * 2, 2)

            tech     = score_technical(df)
            relevant = filter_headlines_for_ticker(headlines, ticker)
            sent     = score_sentiment_keywords(relevant)
            combined = combine_signals(tech, sent, vol_ratio)

            detail = (
                f"RSI:{tech.get('rsi','?'):.1f} "
                f"Trend:{tech.get('trend','?')} "
                f"Vol:{vol_ratio:.1f}x "
                f"Tech:{combined['breakdown']['technical_score']:.2f} "
                f"Sent:{combined['breakdown']['sentiment_score']:.2f}"
            ) if isinstance(tech.get('rsi'), float) else "RSI:? Trend:?"

            return {
                "date":        datetime.now().strftime("%Y-%m-%d %H:%M"),
                "ticker":      ticker,
                "signal":      combined["signal"],
                "score":       round(combined["final_score"], 3),
                "confidence":  combined["confidence"],
                "entry_price": price,
                "sl_price":    sl,
                "target_1":    t1,
                "result":      "OPEN",
                "correct":     "PENDING",
                "detail":      detail,
                "api_cost_usd": 0.0,
            }
        except Exception as e:
            logger.error(f"Strategy A error on {ticker}: {e}")
            return None

    def update_outcome(self, ticker: str, exit_price: float,
                       exit_date: str, signal_date: str):
        """Call when a trade resolves. Updates the CSV row with outcome."""
        rows = load_all(STRATEGY_ID)
        for row in rows:
            if row["ticker"] == ticker and signal_date in row["date"] and row["result"] == "OPEN":
                entry = float(row["entry_price"]) if row["entry_price"] else 0
                t1    = float(row["target_1"])    if row["target_1"]    else 0
                sl    = float(row["sl_price"])    if row["sl_price"]    else 0
                pnl   = round(exit_price - entry, 2)

                row["exit_price"] = exit_price
                row["exit_date"]  = exit_date
                row["pnl"]        = pnl
                row["result"]     = "WIN" if pnl > 0 else "LOSS"
                row["correct"]    = "YES" if (
                    (row["signal"] == "BUY" and exit_price >= t1) or
                    (row["signal"] != "BUY" and exit_price <= sl)
                ) else "NO"
                break
        save_all(STRATEGY_ID, rows)

    def run_forever(self):
        schedule.every().day.at("09:35").do(self.run_scan)
        logger.info("Strategy A scheduled at 09:35 daily")
        self.run_scan()
        while True:
            schedule.run_pending()
            time.sleep(30)


if __name__ == "__main__":
    StrategyA().run_forever()
