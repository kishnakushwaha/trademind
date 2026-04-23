"""
agent/brain.py
The main agent. Orchestrates all modules and makes trade decisions.
Runs on a schedule — scans all watchlist stocks, generates signals,
validates risk, then hands off to paper_trader or live_trader.
"""

import logging
from datetime import datetime

from config.watchlist import WATCHLIST
from config.settings import TRADING_MODE, BUY_SCORE_THRESHOLD
from data.fetcher import (
    fetch_ohlcv, fetch_news_headlines,
    filter_headlines_for_ticker, fetch_live_price
)
from signals.technical import compute_indicators, score_technical
from signals.sentiment import score_sentiment, score_sentiment_keywords
from signals.combiner import combine_signals
from risk.position_sizer import (
    calculate_position, check_daily_loss_limit, check_drawdown_limit
)

logger = logging.getLogger(__name__)


class TradeMindAgent:
    """
    The core AI trading agent.

    Responsibilities:
    - Scan watchlist stocks on schedule
    - Generate trade signals
    - Validate risk before any trade
    - Execute via paper_trader (Phase 3) or live_trader (Phase 4)
    - Send alerts via Telegram
    """

    def __init__(self, capital: float, use_finbert: bool = False):
        """
        Args:
            capital:      starting capital in ₹
            use_finbert:  True = use FinBERT NLP model (slower, more accurate)
                          False = use keyword-based sentiment (faster, no GPU needed)
        """
        self.capital         = capital
        self.peak_capital    = capital
        self.daily_pnl       = 0.0
        self.open_trades     = {}   # {ticker: trade_dict}
        self.use_finbert     = use_finbert
        self.scan_results    = []   # store latest scan for dashboard
        self.mode            = TRADING_MODE

        logger.info(f"TradeMind Agent initialized | Mode: {self.mode} | Capital: ₹{capital}")

    # ── Main scan loop ─────────────────────────────────────────────────────────

    def scan_all(self) -> list[dict]:
        """
        Scan all watchlist stocks. Returns list of signal results.
        Call this on a schedule (e.g. every day at 9:30 AM IST).
        """
        import time
        logger.info(f"Starting scan of {len(WATCHLIST)} stocks...")

        # Fetch news once for all stocks (efficient)
        all_headlines = fetch_news_headlines(max_articles=100)
        results = []

        for i, ticker in enumerate(WATCHLIST):
            try:
                result = self.analyse_stock(ticker, all_headlines)
                results.append(result)

                if result["signal"] == "BUY" and result["confidence"] != "LOW":
                    self._handle_buy_signal(result)

            except Exception as e:
                logger.error(f"Scan failed for {ticker}: {e}")

            # Rate limit: pause briefly every 10 stocks to avoid Yahoo blocking
            if (i + 1) % 10 == 0:
                logger.info(f"Progress: {i+1}/{len(WATCHLIST)} stocks scanned...")
                time.sleep(1)

        self.scan_results = sorted(results, key=lambda x: x["final_score"], reverse=True)
        self._log_scan_summary(self.scan_results)
        return self.scan_results

    def analyse_stock(self, ticker: str, headlines: list[dict] = None) -> dict:
        """
        Full analysis pipeline for one stock.
        Returns a comprehensive signal dict.
        """
        # ── Fetch data ────────────────────────────────────────────────────────
        df = fetch_ohlcv(ticker)
        if df.empty:
            return {"ticker": ticker, "error": "No data", "signal": "HOLD", "final_score": 0.5}

        df = compute_indicators(df)
        if df.empty:
            return {"ticker": ticker, "error": "Indicator error", "signal": "HOLD", "final_score": 0.5}

        latest = df.iloc[-1]
        price  = float(latest["Close"])

        # ── Technical signal ──────────────────────────────────────────────────
        tech = score_technical(df)

        # ── Sentiment signal ──────────────────────────────────────────────────
        relevant_headlines = []
        if headlines:
            relevant_headlines = filter_headlines_for_ticker(headlines, ticker)

        if self.use_finbert:
            sent = score_sentiment(relevant_headlines)
        else:
            sent = score_sentiment_keywords(relevant_headlines)

        # ── Volume ────────────────────────────────────────────────────────────
        vol_ratio = float(latest.get("Volume_ratio", 1.0))

        # ── Combine ───────────────────────────────────────────────────────────
        combined = combine_signals(tech, sent, vol_ratio)

        # ── Position sizing (for reference — not executing yet) ───────────────
        sl_price = round(price * 0.93, 2)   # 7% SL as default
        position = calculate_position(
            price, sl_price,
            available_capital=self.capital,
            current_open_trades=len(self.open_trades)
        )

        return {
            "ticker":       ticker,
            "price":        price,
            "signal":       combined["signal"],
            "confidence":   combined["confidence"],
            "final_score":  combined["final_score"],
            "rsi":          tech.get("rsi"),
            "trend":        tech.get("trend"),
            "vol_ratio":    vol_ratio,
            "position":     position,
            "breakdown":    combined["breakdown"],
            "reasons":      combined["reasons"],
            "timestamp":    datetime.now().isoformat(),
        }

    # ── Trade handling ─────────────────────────────────────────────────────────

    def _handle_buy_signal(self, signal: dict):
        """Process a BUY signal through risk filter, then execute or skip."""

        ticker   = signal["ticker"]
        price    = signal["price"]
        position = signal["position"]

        # Already in this stock
        if ticker in self.open_trades:
            logger.info(f"Already in {ticker} — skipping")
            return

        # Daily loss limit check
        if not check_daily_loss_limit(self.daily_pnl, self.capital):
            logger.warning("Daily loss limit hit. No new trades today.")
            return

        # Drawdown check
        if not check_drawdown_limit(self.peak_capital, self.capital):
            logger.critical("Max drawdown limit hit. Agent halted.")
            return

        # Position validation
        if not position["valid"]:
            logger.warning(f"Trade rejected for {ticker}: {position['rejection']}")
            return

        # ── Execute ───────────────────────────────────────────────────────────
        if self.mode == "paper":
            self._paper_execute(signal, position)
        elif self.mode == "live":
            self._live_execute(signal, position)

    def _paper_execute(self, signal: dict, position: dict):
        """Record a paper trade."""
        from agent.paper_trader import PaperTrader
        pt = PaperTrader()
        trade = pt.open_trade(
            ticker      = signal["ticker"],
            entry_price = signal["price"],
            sl_price    = signal["price"] * 0.93,
            target_1    = position["target_1"],
            target_2    = position["target_2"],
            qty         = position["qty"],
            signal_score = signal["final_score"],
            reasons     = signal["reasons"],
        )
        self.open_trades[signal["ticker"]] = trade
        self.capital -= position["capital_used"]
        logger.info(f"PAPER TRADE OPENED: {signal['ticker']} @ ₹{signal['price']} | qty={position['qty']}")

    def _live_execute(self, signal: dict, position: dict):
        """Execute a live order via Zerodha Kite API."""
        from agent.live_trader import LiveTrader
        lt = LiveTrader()
        lt.place_buy_order(
            ticker    = signal["ticker"],
            qty       = position["qty"],
            sl_price  = round(signal["price"] * 0.93, 2),
            target    = position["target_1"],
        )

    # ── Utilities ──────────────────────────────────────────────────────────────

    def _log_scan_summary(self, results: list[dict]):
        """Print top opportunities from scan."""
        buy_signals = [r for r in results if r.get("signal") == "BUY"]
        logger.info(f"Scan complete: {len(buy_signals)} BUY signals from {len(results)} stocks")
        for r in buy_signals[:3]:
            logger.info(
                f"  {r['ticker']} | Score: {r['final_score']} | "
                f"Confidence: {r['confidence']} | RSI: {r.get('rsi', '?')}"
            )

    def get_portfolio_summary(self) -> dict:
        """Return current portfolio status."""
        return {
            "capital":       self.capital,
            "peak_capital":  self.peak_capital,
            "daily_pnl":     self.daily_pnl,
            "open_trades":   len(self.open_trades),
            "mode":          self.mode,
        }
