"""
main.py — TradeMind Agent Entry Point

Usage:
    # Run full agent (paper mode, scans every day at 9:30 AM IST)
    python main.py

    # Backtest a specific stock
    python main.py --mode backtest --ticker RELIANCE.NS

    # Single scan (no scheduler — good for testing)
    python main.py --mode scan

    # Check paper trade performance
    python main.py --mode performance
"""

import argparse
import logging
import schedule
import time
from datetime import datetime

import colorlog

from config.settings import TOTAL_CAPITAL, TRADING_MODE
from agent.brain import TradeMindAgent
from agent.paper_trader import PaperTrader

# ── Logging setup ──────────────────────────────────────────────────────────────
handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    "%(log_color)s%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    log_colors={
        "DEBUG":    "cyan",
        "INFO":     "green",
        "WARNING":  "yellow",
        "ERROR":    "red",
        "CRITICAL": "bold_red",
    }
))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("main")


# ── Market hours check (IST) ───────────────────────────────────────────────────
def is_market_open() -> bool:
    """NSE trades Mon–Fri, 9:15 AM – 3:30 PM IST."""
    now = datetime.now()
    if now.weekday() >= 5:          # Saturday, Sunday
        return False
    hour, minute = now.hour, now.minute
    return (9 * 60 + 15) <= (hour * 60 + minute) <= (15 * 60 + 30)


# ── Scheduled jobs ─────────────────────────────────────────────────────────────
def morning_scan(agent: TradeMindAgent):
    """Run at 9:30 AM IST — main opportunity scan."""
    from monitor.telegram_bot import send_telegram_msg
    send_telegram_msg("🚀 *TradeMind Morning Scan Started*...")
    
    logger.info("=== MORNING SCAN ===")
    results = agent.scan_all()
    buy_signals = [r for r in results if r.get("signal") == "BUY"]
    
    msg = f"✅ *Scan Complete*\nFound {len(buy_signals)} BUY signals out of {len(results)} stocks."
    if buy_signals:
        msg += "\n\n*Top Picks:*"
        for r in buy_signals[:3]:
            msg += f"\n• {r['ticker']}: Score {r['final_score']} ({r['confidence']})"
    
    send_telegram_msg(msg)
    logger.info(f"Morning scan done: {len(buy_signals)} BUY signals")


def midday_check(agent: TradeMindAgent):
    """Run at 1:00 PM IST — check open positions."""
    open_trades = agent.open_trades
    if not open_trades:
        logger.info("Midday check: no open positions")
        return
    
    from monitor.telegram_bot import send_telegram_msg
    send_telegram_msg(f"📊 *Midday Check*: Monitoring {len(open_trades)} open positions.")
    logger.info(f"Midday check: {len(open_trades)} open positions")


def eod_summary(agent: TradeMindAgent):
    """Run at 3:45 PM IST — end of day summary."""
    from agent.paper_trader import PaperTrader
    pt = PaperTrader()
    summary = pt.get_performance_summary()
    logger.info(f"EOD Summary: {summary}")

    from monitor.telegram_bot import alert_daily_summary
    alert_daily_summary(summary)


# ── Heartbeat Server (for Render Free Tier) ────────────────────────────────────
def run_heartbeat():
    """Simple HTTP server to keep Render Free Tier alive."""
    import http.server
    import socketserver
    import os
    import threading

    PORT = int(os.environ.get("PORT", 8080))
    
    class HeartbeatHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"TradeMind Agent is ALIVE and scanning market...")

    def serve():
        with socketserver.TCPServer(("", PORT), HeartbeatHandler) as httpd:
            logger.info(f"Heartbeat server started on port {PORT}")
            httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="TradeMind AI Agent")
    parser.add_argument("--mode",    default="agent",
                        choices=["agent", "backtest", "scan", "performance"],
                        help="Run mode")
    parser.add_argument("--ticker",  default="RELIANCE.NS",
                        help="Ticker for backtest mode")
    parser.add_argument("--capital", type=float, default=TOTAL_CAPITAL,
                        help="Starting capital in ₹")
    parser.add_argument("--finbert", action="store_true",
                        help="Use FinBERT model for sentiment (slower, more accurate)")
    args = parser.parse_args()

    logger.info(f"TradeMind starting | Mode: {args.mode}")

    # Start heartbeat if running as agent (required for Render)
    if args.mode == "agent":
        run_heartbeat()

    # ── Backtest mode ──────────────────────────────────────────────────────────
    if args.mode == "backtest":
        from backtest.engine import run_backtest
        run_backtest(args.ticker, initial_capital=args.capital)
        return

    # ── Performance review ─────────────────────────────────────────────────────
    if args.mode == "performance":
        pt = PaperTrader()
        summary = pt.get_performance_summary()
        print("\n=== PAPER TRADING PERFORMANCE ===")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        print()
        return

    # ── Single scan mode ───────────────────────────────────────────────────────
    agent = TradeMindAgent(capital=args.capital, use_finbert=args.finbert)

    if args.mode == "scan":
        results = agent.scan_all()
        print(f"\n=== TOP OPPORTUNITIES ({len(results)} stocks scanned) ===")
        for r in results[:10]:
            if "error" not in r:
                print(f"  {r['ticker']:20s} | {r['signal']:4s} | "
                      f"Score: {r['final_score']} | {r['confidence']} | "
                      f"RSI: {r.get('rsi', '?')}")
        return

    # ── Full agent mode (scheduled) ────────────────────────────────────────────
    if args.mode == "agent":
        logger.info(f"Agent running in {TRADING_MODE.upper()} mode")
        logger.info("Scheduled: 9:30 scan | 13:00 midday | 15:45 EOD")

        schedule.every().day.at("09:30").do(morning_scan, agent=agent)
        schedule.every().day.at("13:00").do(midday_check, agent=agent)
        schedule.every().day.at("15:45").do(eod_summary, agent=agent)

        # Run an immediate scan on startup for testing
        logger.info("Running immediate scan on startup...")
        morning_scan(agent)

        while True:
            schedule.run_pending()
            time.sleep(60)


if __name__ == "__main__":
    main()
