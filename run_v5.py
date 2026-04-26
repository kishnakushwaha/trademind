"""
run_v5.py — TradeMind V5 Multi-Strategy Launcher
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Runs all 3 strategies in PARALLEL threads:
  Thread 1: Strategy A (Rule-Based)     — scans at 09:35
  Thread 2: Strategy B (Claude Sonnet)  — scans at 09:40
  Thread 3: Strategy C (Claude Opus)    — scans at 09:45
  Thread 4: Reporter                    — daily 16:00, weekly Friday 16:15
  Thread 5: Telegram heartbeat

Each strategy writes to its OWN CSV log file:
  logs/strategy_a_rulebased.csv
  logs/strategy_b_sonnet.csv
  logs/strategy_c_opus.csv

Reports compare all 3 and tell you which one is winning.

Usage:
    python run_v5.py                     # Run all 3 strategies + reporter (24/7)
    python run_v5.py --scan-now          # Force immediate scan on all 3
    python run_v5.py --strategy A        # Run only Strategy A
    python run_v5.py --strategy B        # Run only Strategy B
    python run_v5.py --strategy C        # Run only Strategy C
    python run_v5.py --report daily      # Print today's comparison report
    python run_v5.py --report weekly     # Print this week's comparison report
"""

import argparse
import logging
import os
import sys
import threading
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

# ── Setup logging ──────────────────────────────────────────────────────────────
import colorlog

handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    "%(log_color)s%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    log_colors={
        "DEBUG": "cyan", "INFO": "green",
        "WARNING": "yellow", "ERROR": "red", "CRITICAL": "bold_red",
    }
))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("V5-Launcher")


# ── Thread runners ─────────────────────────────────────────────────────────────

def run_strategy_a():
    """Thread target: Strategy A (Rule-Based) — costs ₹0."""
    from compare.strategy_a_rulebased import StrategyA
    logger.info("🅰️  Strategy A thread started (Rule-Based)")
    try:
        StrategyA().run_forever()
    except Exception as e:
        logger.error(f"Strategy A crashed: {e}")
        time.sleep(60)
        run_strategy_a()  # auto-restart


def run_strategy_b():
    """Thread target: Strategy B (Claude Sonnet)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("⚠️  ANTHROPIC_API_KEY not set — Strategy B disabled")
        logger.warning("   Set it in .env to enable Sonnet analysis")
        return

    from compare.strategy_b_sonnet import StrategyB
    logger.info("🅱️  Strategy B thread started (Claude Sonnet)")
    try:
        StrategyB().run_forever()
    except Exception as e:
        logger.error(f"Strategy B crashed: {e}")
        time.sleep(60)
        run_strategy_b()


def run_strategy_c():
    """Thread target: Strategy C (Claude Opus)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("⚠️  ANTHROPIC_API_KEY not set — Strategy C disabled")
        logger.warning("   Set it in .env to enable Opus analysis")
        return

    from compare.strategy_c_opus import StrategyC
    logger.info("🅲  Strategy C thread started (Claude Opus)")
    try:
        StrategyC().run_forever()
    except Exception as e:
        logger.error(f"Strategy C crashed: {e}")
        time.sleep(60)
        run_strategy_c()


def run_reporter():
    """Thread target: Daily & weekly comparison reports."""
    from compare.reporter import build_daily_report, build_weekly_report
    import schedule

    schedule.every().day.at("16:00").do(build_daily_report)
    schedule.every().friday.at("16:15").do(build_weekly_report)
    logger.info("📊 Reporter thread started (Daily 16:00 | Weekly Friday 16:15)")

    # Initial report on startup
    try:
        build_daily_report()
    except Exception:
        pass

    while True:
        schedule.run_pending()
        time.sleep(30)


def run_telegram_heartbeat():
    """Send a startup notification to Telegram."""
    try:
        from monitor.telegram_bot import send_message
        api_key = os.getenv("ANTHROPIC_API_KEY")
        strategies = "A (Rule-Based)"
        if api_key:
            strategies += " + B (Sonnet) + C (Opus)"

        send_message(
            f"🚀 TradeMind V5 Started\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Strategies: {strategies}\n"
            f"Watchlist: {_get_watchlist_count()} stocks\n"
            f"Mode: {'LIVE AI' if api_key else 'Rule-Based Only'}\n"
            f"Time: {datetime.now().strftime('%d %b %Y %H:%M IST')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 Reports: Daily 16:00 | Weekly Fri 16:15"
        )
    except Exception as e:
        logger.warning(f"Telegram startup notification failed: {e}")


def _get_watchlist_count() -> int:
    try:
        from config.watchlist import WATCHLIST
        return len(WATCHLIST)
    except Exception:
        return 0


# ── Single strategy runners ───────────────────────────────────────────────────

def scan_now_all():
    """Force an immediate scan on all 3 strategies."""
    from compare.strategy_a_rulebased import StrategyA
    logger.info("━" * 60)
    logger.info("FORCE SCAN — Running all strategies NOW")
    logger.info("━" * 60)

    # Strategy A (always runs)
    logger.info("\n🅰️  Running Strategy A (Rule-Based)...")
    a = StrategyA()
    a.run_scan()

    # Strategy B (if API key available)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        logger.info("\n🅱️  Running Strategy B (Sonnet)...")
        from compare.strategy_b_sonnet import StrategyB
        b = StrategyB()
        b.run_scan()

        logger.info("\n🅲  Running Strategy C (Opus)...")
        from compare.strategy_c_opus import StrategyC
        c = StrategyC()
        c.run_scan()
    else:
        logger.warning("⚠️  Skipping B & C — no ANTHROPIC_API_KEY in .env")

    # Show comparison report
    logger.info("\n📊 Generating comparison report...")
    from compare.reporter import build_daily_report
    build_daily_report()


def scan_single(strategy_id: str):
    """Run a single strategy scan."""
    if strategy_id == "A":
        from compare.strategy_a_rulebased import StrategyA
        StrategyA().run_scan()
    elif strategy_id == "B":
        from compare.strategy_b_sonnet import StrategyB
        StrategyB().run_scan()
    elif strategy_id == "C":
        from compare.strategy_c_opus import StrategyC
        StrategyC().run_scan()
    else:
        print(f"Unknown strategy: {strategy_id}. Use A, B, or C.")


# ── Main entry point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TradeMind V5 — Multi-Strategy AI Trading Agent",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--scan-now", action="store_true",
                        help="Force immediate scan on all strategies")
    parser.add_argument("--strategy", type=str, choices=["A", "B", "C"],
                        help="Run only a specific strategy (A/B/C)")
    parser.add_argument("--report", type=str, choices=["daily", "weekly"],
                        help="Print a comparison report")
    args = parser.parse_args()

    # ── Report mode ────────────────────────────────────────────────────────
    if args.report:
        from compare.reporter import build_daily_report, build_weekly_report
        if args.report == "daily":
            build_daily_report()
        else:
            build_weekly_report()
        return

    # ── Single strategy mode ───────────────────────────────────────────────
    if args.strategy:
        scan_single(args.strategy)
        return

    # ── Force scan mode ────────────────────────────────────────────────────
    if args.scan_now:
        scan_now_all()
        return

    # ── Full 24/7 mode ─────────────────────────────────────────────────────
    print("""
    ╔══════════════════════════════════════════════╗
    ║     TradeMind V5 — Multi-Strategy Agent      ║
    ║                                              ║
    ║  🅰️  Strategy A: Rule-Based (Free)            ║
    ║  🅱️  Strategy B: Claude Sonnet ($)            ║
    ║  🅲  Strategy C: Claude Opus ($$)             ║
    ║  📊 Reporter: Daily + Weekly comparisons      ║
    ║                                              ║
    ║  Each runs in its own thread, writes to its  ║
    ║  own CSV log. They never interfere.           ║
    ╚══════════════════════════════════════════════╝
    """)

    # Send Telegram startup notification
    run_telegram_heartbeat()

    # Launch all threads
    threads = [
        threading.Thread(target=run_strategy_a, name="StrategyA", daemon=True),
        threading.Thread(target=run_strategy_b, name="StrategyB", daemon=True),
        threading.Thread(target=run_strategy_c, name="StrategyC", daemon=True),
        threading.Thread(target=run_reporter,   name="Reporter",  daemon=True),
    ]

    for t in threads:
        t.start()
        logger.info(f"Started thread: {t.name}")
        time.sleep(2)  # stagger starts to avoid API collision

    logger.info("All threads running. Press Ctrl+C to stop.")

    # Keep main thread alive
    try:
        while True:
            # Check thread health every 60 seconds
            for t in threads:
                if not t.is_alive() and t.name != "StrategyB" and t.name != "StrategyC":
                    logger.error(f"Thread {t.name} died! Restarting...")
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down TradeMind V5...")
        sys.exit(0)


if __name__ == "__main__":
    main()
