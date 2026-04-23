"""
agent/auto_executor.py

THIS is the piece that was missing.

This module:
1. Scans watchlist every morning → finds BUY signals
2. Opens paper trades automatically when signal score >= threshold
3. Monitors open positions every hour (or on each run)
4. Closes trades automatically when price hits SL or target
5. Logs everything + sends Telegram alerts

Run this file directly:
    python -m agent.auto_executor

It will loop indefinitely during market hours.
"""

import time
import logging
import schedule
from datetime import datetime, date

import yfinance as yf

from config.settings import (
    TOTAL_CAPITAL, BUY_SCORE_THRESHOLD, TRADING_MODE,
    MAX_OPEN_TRADES, MAX_DAILY_LOSS_PCT
)
from config.watchlist import WATCHLIST
from data.fetcher import fetch_ohlcv, fetch_news_headlines, filter_headlines_for_ticker
from signals.technical import compute_indicators, score_technical
from signals.sentiment import score_sentiment_keywords
from signals.combiner import combine_signals
from risk.position_sizer import calculate_position, check_daily_loss_limit
from agent.paper_trader import PaperTrader

logger = logging.getLogger(__name__)

# ── Telegram (optional — works without it) ────────────────────────────────────
def _send_alert(msg: str):
    try:
        from monitor.telegram_bot import send_message
        send_message(msg)
    except Exception:
        pass   # Telegram not configured — skip silently


class AutoExecutor:
    """
    The complete auto-trading loop.

    Paper mode:  Opens/closes simulated trades. Tracks P&L in CSV.
    Live mode:   Calls Kite API (Phase 4 — only after paper validation).
    """

    def __init__(self, capital: float = TOTAL_CAPITAL):
        self.capital      = capital
        self.peak_capital = capital
        self.daily_pnl    = 0.0
        self.trading_day  = date.today()
        self.pt           = PaperTrader()

        # In-memory map of currently open trades
        # {ticker: {entry_price, sl_price, target_1, target_2, qty, trade_id}}
        self.open_positions: dict = {}

        # Reload any positions that were open from a previous run
        self._reload_open_positions()

        logger.info(
            f"AutoExecutor started | Mode: {TRADING_MODE.upper()} | "
            f"Capital: ₹{self.capital:,.0f} | "
            f"Open positions reloaded: {len(self.open_positions)}"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # PUBLIC: run the full loop
    # ──────────────────────────────────────────────────────────────────────────

    def run_forever(self):
        """
        Schedule jobs and run indefinitely.
        Safe to Ctrl+C — all state is persisted in CSV.
        """
        logger.info("Scheduling jobs...")
        schedule.every().day.at("09:30").do(self._morning_scan)
        schedule.every().hour.do(self._monitor_positions)
        schedule.every().day.at("15:15").do(self._force_close_all_eod)
        schedule.every().day.at("15:45").do(self._eod_summary)

        # Run an immediate scan + monitor on startup
        logger.info("Running startup scan + position check...")
        self._morning_scan()
        self._monitor_positions()

        logger.info("Entering scheduler loop. Press Ctrl+C to stop.")
        while True:
            schedule.run_pending()
            time.sleep(30)

    def run_once(self):
        """
        Single run: scan + monitor. Good for testing and cron jobs.
        """
        self._morning_scan()
        self._monitor_positions()

    # ──────────────────────────────────────────────────────────────────────────
    # STEP 1 — MORNING SCAN: find signals, open trades
    # ──────────────────────────────────────────────────────────────────────────

    def _morning_scan(self):
        """Scan all watchlist stocks and open trades on valid BUY signals."""
        self._reset_daily_pnl_if_new_day()

        if not self._market_is_open():
            logger.info("Market closed — skipping scan")
            return

        logger.info(f"=== MORNING SCAN ({datetime.now().strftime('%d %b %Y %H:%M')}) ===")
        logger.info(f"Open positions: {len(self.open_positions)} / {MAX_OPEN_TRADES}")

        if len(self.open_positions) >= MAX_OPEN_TRADES:
            logger.info("Max positions reached — no new trades today")
            return

        if not check_daily_loss_limit(self.daily_pnl, self.capital):
            logger.warning("Daily loss limit hit — no new trades today")
            return

        headlines = fetch_news_headlines(max_articles=100)

        for ticker in WATCHLIST:
            if ticker in self.open_positions:
                continue   # already in this stock

            if len(self.open_positions) >= MAX_OPEN_TRADES:
                break

            signal = self._generate_signal(ticker, headlines)
            if signal is None:
                continue

            logger.info(
                f"  {ticker:20s} | Score: {signal['final_score']:.3f} | "
                f"{signal['signal']} | {signal['confidence']}"
            )

            if (signal["signal"] == "BUY" and
                signal["confidence"] != "LOW" and
                signal["final_score"] >= BUY_SCORE_THRESHOLD):
                self._open_trade(signal)

        logger.info(
            f"Scan done. Open positions: {len(self.open_positions)} / {MAX_OPEN_TRADES}"
        )

    def _generate_signal(self, ticker: str, headlines: list) -> dict | None:
        """Run the full signal pipeline for one ticker."""
        try:
            df = fetch_ohlcv(ticker)
            if df.empty or len(df) < 60:
                return None

            df = compute_indicators(df)
            if df.empty:
                return None

            latest    = df.iloc[-1]
            price     = float(latest["Close"])
            vol_ratio = float(latest.get("Volume_ratio", 1.0))

            tech = score_technical(df)
            relevant = filter_headlines_for_ticker(headlines, ticker)
            sent = score_sentiment_keywords(relevant)
            combined = combine_signals(tech, sent, vol_ratio)

            return {
                "ticker":      ticker,
                "price":       price,
                "signal":      combined["signal"],
                "confidence":  combined["confidence"],
                "final_score": combined["final_score"],
                "reasons":     combined["reasons"],
                "vol_ratio":   vol_ratio,
            }
        except Exception as e:
            logger.error(f"Signal generation failed for {ticker}: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # STEP 2 — OPEN A TRADE
    # ──────────────────────────────────────────────────────────────────────────

    def _open_trade(self, signal: dict):
        """Open a paper (or live) trade based on a valid BUY signal."""
        ticker = signal["ticker"]
        price  = signal["price"]

        # Calculate SL using swing low from data (simplified: 7% below entry)
        sl_price = round(price * 0.93, 2)

        position = calculate_position(
            entry_price         = price,
            stop_loss_price     = sl_price,
            available_capital   = self.capital,
            current_open_trades = len(self.open_positions),
        )

        if not position["valid"]:
            logger.warning(f"Trade rejected for {ticker}: {position['rejection']}")
            return

        qty      = position["qty"]
        target_1 = position["target_1"]
        target_2 = position["target_2"]

        if TRADING_MODE == "paper":
            trade = self.pt.open_trade(
                ticker       = ticker,
                entry_price  = price,
                sl_price     = sl_price,
                target_1     = target_1,
                target_2     = target_2,
                qty          = qty,
                signal_score = signal["final_score"],
                reasons      = signal["reasons"],
            )
            trade_id = trade["id"]

        elif TRADING_MODE == "live":
            from agent.live_trader import LiveTrader
            lt = LiveTrader()
            order_id = lt.place_buy_order(ticker, qty, sl_price, target_1)
            if not order_id:
                logger.error(f"Live order failed for {ticker}")
                return
            # Still log to paper trader for tracking
            trade = self.pt.open_trade(
                ticker=ticker, entry_price=price,
                sl_price=sl_price, target_1=target_1, target_2=target_2,
                qty=qty, signal_score=signal["final_score"],
                reasons=signal["reasons"] + [f"Live order ID: {order_id}"],
            )
            trade_id = trade["id"]
        else:
            return

        # Store in memory
        self.open_positions[ticker] = {
            "trade_id":    trade_id,
            "entry_price": price,
            "sl_price":    sl_price,
            "target_1":    target_1,
            "target_2":    target_2,
            "qty":         qty,
            "opened_at":   datetime.now().isoformat(),
        }

        self.capital -= position["capital_used"]

        msg = (
            f"{'📄 PAPER' if TRADING_MODE=='paper' else '🟢 LIVE'} TRADE OPENED\n"
            f"Ticker : {ticker}\n"
            f"Entry  : ₹{price:,.2f}\n"
            f"SL     : ₹{sl_price:,.2f} (-7%)\n"
            f"Target : ₹{target_1:,.2f} (+{((target_1-price)/price*100):.1f}%)\n"
            f"Qty    : {qty} shares\n"
            f"Score  : {signal['final_score']:.3f} | {signal['confidence']}\n"
            f"Risk   : ₹{position['risk_amount']:,.0f}"
        )
        logger.info(msg)
        _send_alert(msg)

    # ──────────────────────────────────────────────────────────────────────────
    # STEP 3 — MONITOR POSITIONS: check SL and target
    # ──────────────────────────────────────────────────────────────────────────

    def _monitor_positions(self):
        """
        Check every open position against current live price.
        Close automatically if SL or target is hit.
        """
        if not self.open_positions:
            return

        logger.info(f"Monitoring {len(self.open_positions)} open position(s)...")

        to_close = []   # collect closures to avoid modifying dict while iterating

        for ticker, pos in self.open_positions.items():
            current_price = self._get_live_price(ticker)
            if current_price is None:
                logger.warning(f"Could not fetch live price for {ticker} — skipping")
                continue

            entry    = pos["entry_price"]
            sl       = pos["sl_price"]
            target   = pos["target_1"]
            pct_chg  = ((current_price - entry) / entry) * 100

            logger.info(
                f"  {ticker:20s} | Entry: ₹{entry:.2f} | "
                f"Now: ₹{current_price:.2f} ({pct_chg:+.1f}%) | "
                f"SL: ₹{sl:.2f} | T1: ₹{target:.2f}"
            )

            # ── Stop loss hit ──────────────────────────────────────────────
            if current_price <= sl:
                to_close.append((ticker, current_price, "Stop Loss Hit"))

            # ── Target 1 hit ───────────────────────────────────────────────
            elif current_price >= target:
                to_close.append((ticker, current_price, "Target 1 Reached (1:2 R:R)"))

        for ticker, exit_price, reason in to_close:
            self._close_trade(ticker, exit_price, reason)

    def _get_live_price(self, ticker: str) -> float | None:
        """Fetch the most recent closing price for a ticker."""
        try:
            data = yf.download(ticker, period="1d", interval="5m",
                               progress=False, auto_adjust=True)
            if not data.empty:
                return float(data["Close"].iloc[-1])
            return None
        except Exception as e:
            logger.error(f"Price fetch failed for {ticker}: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # STEP 4 — CLOSE A TRADE
    # ──────────────────────────────────────────────────────────────────────────

    def _close_trade(self, ticker: str, exit_price: float, reason: str):
        """Close a position: update CSV, update capital, send alert."""
        if ticker not in self.open_positions:
            return

        pos      = self.open_positions[ticker]
        trade_id = pos["trade_id"]
        qty      = pos["qty"]
        entry    = pos["entry_price"]

        # Update CSV log
        updated = self.pt.close_trade(trade_id, exit_price, reason)
        pnl     = updated.get("pnl", 0) if updated else round((exit_price - entry) * qty, 2)
        result  = "WIN" if pnl > 0 else "LOSS"

        # Update state
        self.capital   += exit_price * qty
        self.daily_pnl += pnl
        self.peak_capital = max(self.peak_capital, self.capital)
        del self.open_positions[ticker]

        # Live mode: place sell order
        if TRADING_MODE == "live":
            try:
                from agent.live_trader import LiveTrader
                LiveTrader().place_sell_order(ticker, qty)
            except Exception as e:
                logger.error(f"Live sell order failed for {ticker}: {e}")

        msg = (
            f"{'🏆 WIN' if pnl > 0 else '❌ LOSS'} — {ticker}\n"
            f"Entry  : ₹{entry:,.2f}\n"
            f"Exit   : ₹{exit_price:,.2f}\n"
            f"P&L    : ₹{pnl:+,.0f}\n"
            f"Reason : {reason}\n"
            f"Capital: ₹{self.capital:,.0f}"
        )
        logger.info(msg)
        _send_alert(msg)

    # ──────────────────────────────────────────────────────────────────────────
    # EOD: force close all positions before market closes
    # ──────────────────────────────────────────────────────────────────────────

    def _force_close_all_eod(self):
        """
        Close all open positions at 3:15 PM IST.
        Only relevant if you're doing intraday — for swing trading,
        comment this out and let positions carry overnight.
        """
        # For SWING TRADING (our strategy) — do NOT force close EOD
        # Swing trades hold 3-10 days. Comment in only for intraday mode.
        #
        # if self.open_positions:
        #     logger.info("EOD force close — closing all open positions")
        #     for ticker in list(self.open_positions.keys()):
        #         price = self._get_live_price(ticker)
        #         if price:
        #             self._close_trade(ticker, price, "EOD Force Close")
        pass

    def _eod_summary(self):
        """Send end-of-day summary."""
        perf = self.pt.get_performance_summary()
        msg = (
            f"📋 EOD SUMMARY — {date.today().strftime('%d %b %Y')}\n"
            f"Day P&L      : ₹{self.daily_pnl:+,.0f}\n"
            f"Capital      : ₹{self.capital:,.0f}\n"
            f"Open trades  : {len(self.open_positions)}\n"
            f"All-time P&L : ₹{perf.get('total_pnl', 0):+,.0f}\n"
            f"Win rate     : {perf.get('win_rate_pct', '—')}%"
        )
        logger.info(msg)
        _send_alert(msg)

    # ──────────────────────────────────────────────────────────────────────────
    # UTILITIES
    # ──────────────────────────────────────────────────────────────────────────

    def _market_is_open(self) -> bool:
        """NSE: Mon–Fri 9:15 AM – 3:30 PM IST."""
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        minutes = now.hour * 60 + now.minute
        return (9 * 60 + 15) <= minutes <= (15 * 60 + 30)

    def _reset_daily_pnl_if_new_day(self):
        """Reset daily P&L counter at start of each trading day."""
        today = date.today()
        if today != self.trading_day:
            logger.info(f"New trading day: {today} — resetting daily P&L")
            self.daily_pnl   = 0.0
            self.trading_day = today

    def _reload_open_positions(self):
        """
        On restart, reload any OPEN trades from CSV so we don't
        lose track of positions that were opened in a previous session.
        """
        open_trades = self.pt.get_open_trades()
        for t in open_trades:
            ticker = t.get("ticker", "")
            if ticker:
                self.open_positions[ticker] = {
                    "trade_id":    int(t["id"]),
                    "entry_price": float(t["entry_price"]),
                    "sl_price":    float(t["sl_price"]),
                    "target_1":    float(t["target_1"]),
                    "target_2":    float(t["target_2"]),
                    "qty":         int(t["qty"]),
                    "opened_at":   t.get("entry_date", ""),
                }
        if self.open_positions:
            logger.info(f"Reloaded {len(self.open_positions)} open position(s) from previous session")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import colorlog

    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        log_colors={"DEBUG":"cyan","INFO":"green","WARNING":"yellow",
                    "ERROR":"red","CRITICAL":"bold_red"}
    ))
    logging.basicConfig(level=logging.INFO, handlers=[handler])

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="Run one scan+monitor cycle and exit (for testing)")
    args = parser.parse_args()

    executor = AutoExecutor(capital=TOTAL_CAPITAL)

    if args.once:
        executor.run_once()
    else:
        executor.run_forever()
