"""
backtest/engine.py
Backtest any strategy on historical data BEFORE risking real money.
Do not skip this step. Your strategy must show 55%+ win rate here first.

Usage:
    python -m backtest.engine --ticker RELIANCE.NS --period 2y
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime

from data.fetcher import fetch_ohlcv
from signals.technical import compute_indicators, score_technical
from risk.position_sizer import calculate_position
from config.settings import BACKTEST_RESULTS_PATH, BUY_SCORE_THRESHOLD

logger = logging.getLogger(__name__)


def run_backtest(ticker: str, initial_capital: float = 10000,
                 period: str = "2y") -> dict:
    """
    Run a full backtest on historical data.

    Strategy simulated:
    - BUY when technical score >= BUY_SCORE_THRESHOLD
    - Exit at T1 (1:2 R:R target) OR stop loss
    - Max 1 position at a time (for simplicity)

    Args:
        ticker:          e.g. "RELIANCE.NS"
        initial_capital: starting paper capital in ₹
        period:          yfinance period string ("1y", "2y", etc.)

    Returns:
        dict with performance metrics
    """
    logger.info(f"Starting backtest: {ticker} | Capital: ₹{initial_capital} | Period: {period}")

    # ── Fetch and prepare data ────────────────────────────────────────────────
    df = fetch_ohlcv(ticker, period=period)
    if df.empty or len(df) < 60:
        return {"error": "Insufficient historical data"}

    df = compute_indicators(df)
    if df.empty:
        return {"error": "Indicator calculation failed"}

    # ── Simulation ────────────────────────────────────────────────────────────
    capital       = initial_capital
    peak_capital  = initial_capital
    trades        = []
    in_trade      = False
    entry_price   = 0
    sl_price      = 0
    target_price  = 0
    qty           = 0
    entry_date    = None

    for i in range(1, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]
        date = df.index[i]
        price = float(row["Close"])
        high  = float(row["High"])
        low   = float(row["Low"])

        # ── If in a trade: check exit conditions ──────────────────────────────
        if in_trade:
            # Stop loss hit (price touched SL intraday via Low)
            if low <= sl_price:
                pnl      = (sl_price - entry_price) * qty
                capital += pnl
                trades.append({
                    "ticker":      ticker,
                    "entry_date":  entry_date,
                    "exit_date":   date,
                    "entry_price": entry_price,
                    "exit_price":  sl_price,
                    "qty":         qty,
                    "pnl":         round(pnl, 2),
                    "result":      "LOSS",
                    "exit_reason": "Stop Loss",
                })
                in_trade = False

            # Target hit (price touched T1 intraday via High)
            elif high >= target_price:
                pnl      = (target_price - entry_price) * qty
                capital += pnl
                trades.append({
                    "ticker":      ticker,
                    "entry_date":  entry_date,
                    "exit_date":   date,
                    "entry_price": entry_price,
                    "exit_price":  target_price,
                    "qty":         qty,
                    "pnl":         round(pnl, 2),
                    "result":      "WIN",
                    "exit_reason": "Target 1 (1:2 R:R)",
                })
                in_trade = False

        # ── If not in trade: check entry signal ───────────────────────────────
        else:
            sub_df = df.iloc[:i + 1]
            tech   = score_technical(sub_df)
            signal_score = tech["score"]

            if signal_score >= BUY_SCORE_THRESHOLD and tech["trend"] == "up":
                # Use ATR-based SL (7% below entry as simple proxy)
                sl  = round(price * 0.93, 2)
                pos = calculate_position(price, sl, capital)

                if pos["valid"] and pos["qty"] > 0:
                    entry_price  = price
                    sl_price     = sl
                    target_price = pos["target_1"]
                    qty          = pos["qty"]
                    entry_date   = date
                    capital     -= pos["capital_used"]
                    in_trade     = True

        peak_capital = max(peak_capital, capital)

    # ── Close any open trade at end of period ─────────────────────────────────
    if in_trade:
        last_price = float(df.iloc[-1]["Close"])
        pnl = (last_price - entry_price) * qty
        capital += (last_price * qty)
        trades.append({
            "ticker":      ticker,
            "entry_date":  entry_date,
            "exit_date":   df.index[-1],
            "entry_price": entry_price,
            "exit_price":  last_price,
            "qty":         qty,
            "pnl":         round(pnl, 2),
            "result":      "WIN" if pnl > 0 else "LOSS",
            "exit_reason": "End of period",
        })

    # ── Metrics ───────────────────────────────────────────────────────────────
    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {"error": "No trades generated — check signal thresholds"}

    wins          = (trades_df["result"] == "WIN").sum()
    losses        = (trades_df["result"] == "LOSS").sum()
    total_trades  = len(trades_df)
    win_rate      = round((wins / total_trades) * 100, 1) if total_trades else 0
    total_pnl     = round(trades_df["pnl"].sum(), 2)
    avg_win       = round(trades_df[trades_df["result"]=="WIN"]["pnl"].mean(), 2) if wins else 0
    avg_loss      = round(trades_df[trades_df["result"]=="LOSS"]["pnl"].mean(), 2) if losses else 0
    max_drawdown  = round(((peak_capital - capital) / peak_capital) * 100, 1)
    total_return  = round(((capital - initial_capital) / initial_capital) * 100, 1)

    # Expectancy: (win_rate × avg_win) + (loss_rate × avg_loss)
    loss_rate  = (losses / total_trades) if total_trades else 0
    win_rate_d = (wins / total_trades) if total_trades else 0
    expectancy = round((win_rate_d * avg_win) + (loss_rate * avg_loss), 2)

    metrics = {
        "ticker":          ticker,
        "period":          period,
        "initial_capital": initial_capital,
        "final_capital":   round(capital, 2),
        "total_return_pct": total_return,
        "total_pnl":       total_pnl,
        "total_trades":    total_trades,
        "wins":            int(wins),
        "losses":          int(losses),
        "win_rate_pct":    win_rate,
        "avg_win":         avg_win,
        "avg_loss":        avg_loss,
        "expectancy":      expectancy,
        "max_drawdown_pct": max_drawdown,
        "trades":          trades,
    }

    _print_backtest_report(metrics)
    _save_results(trades_df, ticker)
    return metrics


def _print_backtest_report(m: dict):
    """Print a clean backtest summary."""
    print("\n" + "="*50)
    print(f"BACKTEST RESULTS — {m['ticker']} ({m['period']})")
    print("="*50)
    print(f"Initial capital : ₹{m['initial_capital']:,.0f}")
    print(f"Final capital   : ₹{m['final_capital']:,.0f}")
    print(f"Total return    : {m['total_return_pct']}%")
    print(f"Total P&L       : ₹{m['total_pnl']:,.0f}")
    print(f"Total trades    : {m['total_trades']}")
    print(f"Win rate        : {m['win_rate_pct']}%  ({m['wins']}W / {m['losses']}L)")
    print(f"Avg win         : ₹{m['avg_win']}")
    print(f"Avg loss        : ₹{m['avg_loss']}")
    print(f"Expectancy/trade: ₹{m['expectancy']}")
    print(f"Max drawdown    : {m['max_drawdown_pct']}%")
    verdict = "✓ STRATEGY VALID" if m['win_rate_pct'] >= 55 and m['expectancy'] > 0 else "✗ STRATEGY NEEDS WORK"
    print(f"\nVerdict: {verdict}")
    print("="*50 + "\n")


def _save_results(trades_df: pd.DataFrame, ticker: str):
    """Save backtest trades to CSV."""
    try:
        path = BACKTEST_RESULTS_PATH.replace(".csv", f"_{ticker.replace('.', '_')}.csv")
        trades_df.to_csv(path, index=False)
        logger.info(f"Backtest results saved to {path}")
    except Exception as e:
        logger.warning(f"Could not save backtest results: {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TradeMind Backtester")
    parser.add_argument("--ticker", default="RELIANCE.NS", help="NSE ticker (e.g. RELIANCE.NS)")
    parser.add_argument("--capital", type=float, default=10000, help="Starting capital in ₹")
    parser.add_argument("--period", default="2y", help="History period (1y, 2y, 5y)")
    args = parser.parse_args()
    run_backtest(args.ticker, args.capital, args.period)
