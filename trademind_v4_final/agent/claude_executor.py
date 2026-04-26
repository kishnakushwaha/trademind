"""
agent/claude_executor.py

The complete agent — but now every trade decision goes through
the actual Claude API instead of rule-based signal logic.

This is the "give Claude access to trade" version.
Claude sees the real market data and makes the call.
Your machine executes it via Kite.

Setup:
    1. pip install anthropic
    2. Add to .env: ANTHROPIC_API_KEY=sk-ant-...
       Get key from: https://console.anthropic.com/
    3. python -m agent.claude_executor

Cost: Each stock analysis = ~1 API call (~1000 tokens).
      Scanning 15 stocks daily = ~15 calls = roughly ₹2-3/day at current pricing.
"""

import time
import logging
import schedule
from datetime import datetime, date

import yfinance as yf

from config.settings import TOTAL_CAPITAL, TRADING_MODE, MAX_OPEN_TRADES
from config.watchlist import WATCHLIST
from data.fetcher import fetch_news_headlines
from risk.position_sizer import calculate_position, check_daily_loss_limit
from agent.paper_trader import PaperTrader
from agent.claude_brain import get_claude_decision_sync, claude_portfolio_review

logger = logging.getLogger(__name__)


def _send_alert(msg: str):
    try:
        from monitor.telegram_bot import send_message
        send_message(msg)
    except Exception:
        pass


class ClaudeExecutor:
    """
    Auto-trading agent where every decision is made by Claude API.

    Flow:
    1. Fetch market data for each watchlist stock
    2. Send data to Claude → get structured decision
    3. If Claude says BUY → validate risk → open trade
    4. Every hour → check positions → Claude reviews → close if needed
    5. Telegram alert on every action
    """

    def __init__(self, capital: float = TOTAL_CAPITAL):
        self.capital        = capital
        self.peak_capital   = capital
        self.daily_pnl      = 0.0
        self.trading_day    = date.today()
        self.pt             = PaperTrader()
        self.open_positions : dict = {}
        self._reload_open_positions()

        logger.info(
            f"ClaudeExecutor ready | Mode: {TRADING_MODE.upper()} | "
            f"Capital: ₹{self.capital:,.0f} | "
            f"Reloaded positions: {len(self.open_positions)}"
        )

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run_forever(self):
        schedule.every().day.at("09:30").do(self._morning_scan)
        schedule.every().day.at("12:30").do(self._midday_review)
        schedule.every().day.at("15:45").do(self._eod_summary)

        logger.info("Running startup cycle...")
        self._morning_scan()

        logger.info("Scheduler active. Ctrl+C to stop.")
        while True:
            schedule.run_pending()
            time.sleep(30)

    def run_once(self):
        """Single scan + monitor cycle. Good for testing."""
        self._morning_scan()
        self._midday_review()

    # ── Morning scan ───────────────────────────────────────────────────────────

    def _morning_scan(self):
        self._reset_daily_if_new_day()

        if not self._market_open():
            logger.info("Market closed — skipping scan")
            return

        logger.info(f"=== CLAUDE SCAN ({datetime.now().strftime('%d %b %Y %H:%M')}) ===")

        if len(self.open_positions) >= MAX_OPEN_TRADES:
            logger.info(f"Max positions ({MAX_OPEN_TRADES}) reached — no new trades")
            return

        if not check_daily_loss_limit(self.daily_pnl, self.capital):
            logger.warning("Daily loss limit hit — halting new trades")
            return

        # Fetch news once for all stocks
        headlines = fetch_news_headlines(max_articles=100)

        for ticker in WATCHLIST:
            if ticker in self.open_positions:
                continue
            if len(self.open_positions) >= MAX_OPEN_TRADES:
                break

            # ── Claude makes the call ──────────────────────────────────────
            decision = get_claude_decision_sync(ticker, headlines)

            self._log_decision(ticker, decision)

            if (decision.get("decision") == "BUY" and
                decision.get("confidence") != "LOW" and
                decision.get("score", 0) >= 0.65):
                self._open_trade(ticker, decision)

            # Small delay between API calls — avoid rate limits
            time.sleep(2)

    # ── Midday portfolio review ────────────────────────────────────────────────

    def _midday_review(self):
        """Ask Claude to review all open positions and suggest exits."""
        if not self.open_positions:
            return

        logger.info("=== MIDDAY REVIEW ===")

        # Update current prices first
        self._update_prices()

        # Ask Claude for portfolio review
        review = claude_portfolio_review(self.open_positions, self.daily_pnl)

        positions_review = review.get("positions", {})

        for ticker, rec in positions_review.items():
            if ticker not in self.open_positions:
                continue

            action = rec.get("action", "HOLD")
            reason = rec.get("reason", "")

            if action == "EXIT":
                price = self._live_price(ticker)
                if price:
                    logger.info(f"Claude recommends EXIT {ticker}: {reason}")
                    self._close_trade(ticker, price, f"Claude review: {reason}")

            elif action == "TRAIL_SL":
                new_sl = rec.get("new_sl")
                if new_sl and new_sl > self.open_positions[ticker]["sl_price"]:
                    old_sl = self.open_positions[ticker]["sl_price"]
                    self.open_positions[ticker]["sl_price"] = new_sl
                    logger.info(f"Trailing SL for {ticker}: ₹{old_sl:.2f} → ₹{new_sl:.2f}")
                    _send_alert(f"📈 SL Trailed — {ticker}\n₹{old_sl:.2f} → ₹{new_sl:.2f}\n{reason}")

        notes = review.get("overall_notes", "")
        if notes:
            logger.info(f"Claude portfolio note: {notes}")

    def _update_prices(self):
        """Check each open position against live price — close if SL/target hit."""
        for ticker in list(self.open_positions.keys()):
            price = self._live_price(ticker)
            if not price:
                continue

            pos    = self.open_positions[ticker]
            entry  = pos["entry_price"]
            sl     = pos["sl_price"]
            target = pos["target_1"]
            pct    = ((price - entry) / entry) * 100

            logger.info(
                f"  {ticker:20s} | ₹{entry:.2f} → ₹{price:.2f} "
                f"({pct:+.1f}%) | SL: ₹{sl:.2f} | T1: ₹{target:.2f}"
            )

            if price <= sl:
                self._close_trade(ticker, price, "Stop Loss Hit")
            elif price >= target:
                self._close_trade(ticker, price, "Target 1 Hit (1:2 R:R)")

    # ── Open trade ─────────────────────────────────────────────────────────────

    def _open_trade(self, ticker: str, decision: dict):
        """Execute a trade based on Claude's BUY decision."""

        # Use Claude's suggested entry/SL/target, fallback to calculated
        entry_zone = decision.get("entry_zone", {})
        entry_price = entry_zone.get("high", None)   # use upper entry zone

        # Get live price if Claude didn't specify clearly
        if not entry_price or entry_price <= 0:
            entry_price = self._live_price(ticker)
        if not entry_price:
            logger.warning(f"Could not determine entry price for {ticker}")
            return

        sl_price  = decision.get("stop_loss") or round(entry_price * 0.93, 2)
        target_1  = decision.get("target_1")  or round(entry_price + (entry_price - sl_price) * 2, 2)
        target_2  = decision.get("target_2")  or round(entry_price + (entry_price - sl_price) * 3, 2)

        position = calculate_position(
            entry_price         = entry_price,
            stop_loss_price     = sl_price,
            available_capital   = self.capital,
            current_open_trades = len(self.open_positions),
        )

        if not position["valid"]:
            logger.warning(f"Risk rejected {ticker}: {position['rejection']}")
            return

        qty = position["qty"]

        if TRADING_MODE == "paper":
            reasoning = decision.get("reasoning", {})
            reasons   = [f"{k}: {v}" for k, v in reasoning.items()]

            trade = self.pt.open_trade(
                ticker       = ticker,
                entry_price  = entry_price,
                sl_price     = sl_price,
                target_1     = target_1,
                target_2     = target_2,
                qty          = qty,
                signal_score = decision.get("score", 0.65),
                reasons      = reasons,
            )
            trade_id = trade["id"]

        elif TRADING_MODE == "live":
            from agent.live_trader import LiveTrader
            lt = LiveTrader()
            order_id = lt.place_buy_order(ticker, qty, sl_price, target_1)
            if not order_id:
                logger.error(f"Live order failed for {ticker}")
                return
            trade = self.pt.open_trade(
                ticker=ticker, entry_price=entry_price,
                sl_price=sl_price, target_1=target_1, target_2=target_2,
                qty=qty, signal_score=decision.get("score", 0.65),
                reasons=[f"Live order: {order_id}"],
            )
            trade_id = trade["id"]
        else:
            return

        self.open_positions[ticker] = {
            "trade_id":    trade_id,
            "entry_price": entry_price,
            "sl_price":    sl_price,
            "target_1":    target_1,
            "target_2":    target_2,
            "qty":         qty,
            "opened_at":   datetime.now().isoformat(),
        }
        self.capital -= position["capital_used"]

        r = decision.get("reasoning", {})
        msg = (
            f"🧠 CLAUDE {'PAPER' if TRADING_MODE=='paper' else 'LIVE'} TRADE\n"
            f"Stock  : {ticker}\n"
            f"Entry  : ₹{entry_price:,.2f}\n"
            f"SL     : ₹{sl_price:,.2f}\n"
            f"Target : ₹{target_1:,.2f}\n"
            f"Qty    : {qty} | Risk: ₹{position['risk_amount']:,.0f}\n"
            f"Score  : {decision.get('score', '?')} | {decision.get('confidence','?')}\n"
            f"Trend  : {r.get('trend', '')}\n"
            f"Momentum: {r.get('momentum', '')}"
        )
        logger.info(msg)
        _send_alert(msg)

    # ── Close trade ────────────────────────────────────────────────────────────

    def _close_trade(self, ticker: str, exit_price: float, reason: str):
        if ticker not in self.open_positions:
            return

        pos      = self.open_positions[ticker]
        trade_id = pos["trade_id"]
        qty      = pos["qty"]
        entry    = pos["entry_price"]

        updated = self.pt.close_trade(trade_id, exit_price, reason)
        pnl     = updated.get("pnl", round((exit_price - entry) * qty, 2)) if updated else round((exit_price - entry) * qty, 2)

        self.capital   += exit_price * qty
        self.daily_pnl += pnl
        self.peak_capital = max(self.peak_capital, self.capital)
        del self.open_positions[ticker]

        if TRADING_MODE == "live":
            try:
                from agent.live_trader import LiveTrader
                LiveTrader().place_sell_order(ticker, qty)
            except Exception as e:
                logger.error(f"Live sell failed for {ticker}: {e}")

        msg = (
            f"{'🏆' if pnl > 0 else '❌'} TRADE CLOSED — {ticker}\n"
            f"Entry  : ₹{entry:,.2f}\n"
            f"Exit   : ₹{exit_price:,.2f}\n"
            f"P&L    : ₹{pnl:+,.0f}\n"
            f"Reason : {reason}\n"
            f"Capital: ₹{self.capital:,.0f}"
        )
        logger.info(msg)
        _send_alert(msg)

    # ── Utilities ──────────────────────────────────────────────────────────────

    def _live_price(self, ticker: str) -> float | None:
        try:
            df = yf.download(ticker, period="1d", interval="5m",
                             progress=False, auto_adjust=True)
            return float(df["Close"].iloc[-1]) if not df.empty else None
        except Exception:
            return None

    def _market_open(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        m = now.hour * 60 + now.minute
        return (9 * 60 + 15) <= m <= (15 * 60 + 30)

    def _reset_daily_if_new_day(self):
        today = date.today()
        if today != self.trading_day:
            self.daily_pnl   = 0.0
            self.trading_day = today

    def _reload_open_positions(self):
        for t in self.pt.get_open_trades():
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

    def _log_decision(self, ticker: str, decision: dict):
        flags = decision.get("red_flags", [])
        flag_str = f" ⚠ {', '.join(flags)}" if flags else ""
        logger.info(
            f"  {ticker:20s} | {decision.get('decision','?'):5s} | "
            f"Score: {decision.get('score', 0):.3f} | "
            f"{decision.get('confidence','?')}{flag_str}"
        )

    def _eod_summary(self):
        perf = self.pt.get_performance_summary()
        msg = (
            f"📋 EOD — {date.today().strftime('%d %b %Y')}\n"
            f"Day P&L  : ₹{self.daily_pnl:+,.0f}\n"
            f"Capital  : ₹{self.capital:,.0f}\n"
            f"Open     : {len(self.open_positions)}\n"
            f"Win rate : {perf.get('win_rate_pct','—')}%\n"
            f"Total P&L: ₹{perf.get('total_pnl', 0):+,.0f}"
        )
        logger.info(msg)
        _send_alert(msg)


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

    parser = argparse.ArgumentParser(description="Claude-Powered Trading Agent")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    executor = ClaudeExecutor(capital=TOTAL_CAPITAL)

    if args.once:
        executor.run_once()
    else:
        executor.run_forever()
