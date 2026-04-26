"""
monitor/telegram_bot.py
Sends trade alerts to your Telegram.

Setup:
1. Message @BotFather on Telegram → /newbot → get token
2. Message your bot once → get chat ID from https://api.telegram.org/bot<TOKEN>/getUpdates
3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

import requests
import logging
from datetime import datetime

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_message(text: str) -> bool:
    """Send a plain text message to your Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not set — alert skipped")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def alert_buy_signal(ticker: str, price: float, sl: float,
                     target: float, score: float, confidence: str,
                     reasons: list[str]) -> bool:
    """Send a BUY signal alert."""
    sl_pct  = round(((price - sl) / price) * 100, 1)
    rr_gain = round(((target - price) / price) * 100, 1)

    msg = (
        f"🟢 <b>BUY SIGNAL — {ticker}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Entry   : ₹{price}\n"
        f"🛑 SL      : ₹{sl} (-{sl_pct}%)\n"
        f"🎯 Target  : ₹{target} (+{rr_gain}%)\n"
        f"📊 Score   : {score} | {confidence} confidence\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Reasons:</i>\n"
        + "\n".join(f"• {r}" for r in reasons[:4]) +
        f"\n━━━━━━━━━━━━━━━━━━\n"
        f"<i>PAPER TRADE — not real money</i>\n"
        f"<i>{datetime.now().strftime('%d %b %Y %H:%M IST')}</i>"
    )
    return send_message(msg)


def alert_trade_closed(ticker: str, entry: float, exit_price: float,
                       qty: int, pnl: float, reason: str) -> bool:
    """Send a trade closure alert."""
    result = "WIN 🏆" if pnl > 0 else "LOSS ❌"
    msg = (
        f"{'🟢' if pnl > 0 else '🔴'} <b>TRADE CLOSED — {ticker}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Result  : {result}\n"
        f"Entry   : ₹{entry}\n"
        f"Exit    : ₹{exit_price}\n"
        f"Qty     : {qty} shares\n"
        f"P&L     : ₹{pnl:+.0f}\n"
        f"Reason  : {reason}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>{datetime.now().strftime('%d %b %Y %H:%M IST')}</i>"
    )
    return send_message(msg)


def alert_risk_breach(message: str) -> bool:
    """Send a risk/system alert."""
    msg = f"⚠️ <b>RISK ALERT</b>\n\n{message}\n\n<i>TradeMind Agent</i>"
    return send_message(msg)


def alert_daily_summary(summary: dict) -> bool:
    """Send end-of-day performance summary."""
    pnl    = summary.get("total_pnl", 0)
    trades = summary.get("total_trades", 0)
    wins   = summary.get("wins", 0)
    losses = summary.get("losses", 0)

    msg = (
        f"📋 <b>Daily Summary — TradeMind</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Trades today : {trades}\n"
        f"W/L          : {wins}W / {losses}L\n"
        f"Day P&L      : ₹{pnl:+.0f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>{datetime.now().strftime('%d %b %Y')}</i>"
    )
    return send_message(msg)
