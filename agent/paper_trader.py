"""
agent/paper_trader.py
Simulates trade execution with a trade log. No real money involved.
Run this for 60+ days before touching live execution.

All trades logged to logs/trades.csv
"""

import csv
import os
import logging
from datetime import datetime

from config.settings import TRADE_LOG_PATH

logger = logging.getLogger(__name__)

TRADE_LOG_HEADERS = [
    "id", "ticker", "mode", "entry_date", "exit_date",
    "entry_price", "exit_price", "sl_price", "target_1", "target_2",
    "qty", "pnl", "result", "exit_reason",
    "signal_score", "reasons", "status"
]


class PaperTrader:
    """
    Records paper trades to CSV. Simulates entry/exit without API calls.
    """

    def __init__(self):
        self._ensure_log_exists()
        self._trade_counter = self._get_last_id() + 1

    def _ensure_log_exists(self):
        os.makedirs(os.path.dirname(TRADE_LOG_PATH), exist_ok=True)
        if not os.path.exists(TRADE_LOG_PATH):
            with open(TRADE_LOG_PATH, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=TRADE_LOG_HEADERS)
                writer.writeheader()

    def _get_last_id(self) -> int:
        try:
            with open(TRADE_LOG_PATH, "r") as f:
                rows = list(csv.DictReader(f))
                return int(rows[-1]["id"]) if rows else 0
        except Exception:
            return 0

    def open_trade(self, ticker: str, entry_price: float, sl_price: float,
                   target_1: float, target_2: float, qty: int,
                   signal_score: float = 0.0, reasons: list = None) -> dict:
        """Log a new paper trade as OPEN."""
        trade = {
            "id":           self._trade_counter,
            "ticker":       ticker,
            "mode":         "paper",
            "entry_date":   datetime.now().strftime("%Y-%m-%d %H:%M"),
            "exit_date":    "",
            "entry_price":  entry_price,
            "exit_price":   "",
            "sl_price":     sl_price,
            "target_1":     target_1,
            "target_2":     target_2,
            "qty":          qty,
            "pnl":          "",
            "result":       "",
            "exit_reason":  "",
            "signal_score": signal_score,
            "reasons":      " | ".join(reasons or []),
            "status":       "OPEN",
        }
        self._append_trade(trade)
        self._trade_counter += 1
        
        # Telegram alert
        from monitor.telegram_bot import send_message
        msg = (f"🔵 NEW PAPER TRADE\n\n"
               f"Stock: {ticker}\n"
               f"Entry: ₹{entry_price}\n"
               f"Qty: {qty}\n"
               f"SL: ₹{sl_price}\n"
               f"Target 1: ₹{target_1}\n"
               f"Target 2: ₹{target_2}\n"
               f"Score: {signal_score}")
        send_message(msg)
        
        logger.info(f"Paper trade #{trade['id']} opened: {ticker} @ ₹{entry_price}")
        return trade

    def close_trade(self, trade_id: int, exit_price: float,
                    exit_reason: str = "Manual") -> dict:
        """Update a paper trade as CLOSED with P&L."""
        trades = self._load_all_trades()
        updated = None

        for trade in trades:
            if int(trade["id"]) == trade_id:
                entry  = float(trade["entry_price"])
                qty    = int(trade["qty"])
                pnl    = round((exit_price - entry) * qty, 2)
                ticker = trade["ticker"]

                trade["exit_date"]   = datetime.now().strftime("%Y-%m-%d %H:%M")
                trade["exit_price"]  = exit_price
                trade["pnl"]         = pnl
                trade["result"]      = "WIN" if pnl > 0 else "LOSS"
                trade["exit_reason"] = exit_reason
                trade["status"]      = "CLOSED"
                updated = trade
                break

        if updated:
            self._save_all_trades(trades)
            
            # Telegram alert
            from monitor.telegram_bot import send_message
            result_icon = "🟢" if updated["result"] == "WIN" else "🔴"
            msg = (f"{result_icon} PAPER TRADE CLOSED\n\n"
                   f"Stock: {ticker}\n"
                   f"Exit: ₹{exit_price}\n"
                   f"Reason: {exit_reason}\n"
                   f"P&L: ₹{updated['pnl']} ({updated['result']})")
            send_message(msg)
            
            logger.info(
                f"Paper trade #{trade_id} closed @ ₹{exit_price} | "
                f"P&L: ₹{updated['pnl']} ({updated['result']})"
            )
        return updated or {}

    def get_open_trades(self) -> list[dict]:
        return [t for t in self._load_all_trades() if t["status"] == "OPEN"]

    def get_performance_summary(self) -> dict:
        trades = self._load_all_trades()
        closed = [t for t in trades if t["status"] == "CLOSED"]
        if not closed:
            return {"message": "No closed trades yet"}

        wins     = [t for t in closed if t["result"] == "WIN"]
        losses   = [t for t in closed if t["result"] == "LOSS"]
        total_pnl = sum(float(t["pnl"]) for t in closed)
        win_rate  = round(len(wins) / len(closed) * 100, 1)

        return {
            "total_trades": len(closed),
            "wins":         len(wins),
            "losses":       len(losses),
            "win_rate_pct": win_rate,
            "total_pnl":    round(total_pnl, 2),
            "avg_win":      round(sum(float(t["pnl"]) for t in wins)   / len(wins),   2) if wins   else 0,
            "avg_loss":     round(sum(float(t["pnl"]) for t in losses) / len(losses), 2) if losses else 0,
        }

    def _load_all_trades(self) -> list[dict]:
        with open(TRADE_LOG_PATH, "r") as f:
            return list(csv.DictReader(f))

    def _save_all_trades(self, trades: list[dict]):
        with open(TRADE_LOG_PATH, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_LOG_HEADERS)
            writer.writeheader()
            writer.writerows(trades)

    def _append_trade(self, trade: dict):
        with open(TRADE_LOG_PATH, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_LOG_HEADERS)
            writer.writerow(trade)
