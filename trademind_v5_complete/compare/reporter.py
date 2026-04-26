"""
compare/reporter.py
━━━━━━━━━━━━━━━━━━
Report Generator — reads all 3 strategy logs and produces:

  DAILY  report: every evening at 16:00 IST
  WEEKLY report: every Friday at 16:15 IST (deeper analysis)

Reports are saved to logs/reports/ and optionally sent via Telegram.

Run standalone:
    python -m compare.reporter --daily     # print daily report now
    python -m compare.reporter --weekly    # print weekly report now
    python -m compare.reporter             # scheduled mode
"""

import os
import json
import schedule
import time
from datetime import datetime, date, timedelta
from collections import defaultdict

from compare.shared_logger import load_all, get_logger

logger = get_logger("Reporter")
REPORTS_DIR = "logs/reports"


def _compute_metrics(rows: list[dict], label: str) -> dict:
    """Compute performance metrics from a list of signal rows."""
    closed   = [r for r in rows if r.get("result") in ("WIN", "LOSS")]
    open_    = [r for r in rows if r.get("result") == "OPEN"]
    buy_sig  = [r for r in rows if r.get("signal") == "BUY"]
    correct  = [r for r in closed if r.get("correct") == "YES"]
    wins     = [r for r in closed if r.get("result") == "WIN"]
    losses   = [r for r in closed if r.get("result") == "LOSS"]

    pnls     = [float(r["pnl"]) for r in closed if r.get("pnl") not in ("", None)]
    win_pnls = [float(r["pnl"]) for r in wins   if r.get("pnl") not in ("", None)]
    los_pnls = [float(r["pnl"]) for r in losses if r.get("pnl") not in ("", None)]

    costs    = [float(r.get("api_cost_usd", 0)) for r in rows]
    total_cost_usd = sum(costs)

    # Expectancy = avg P&L per resolved signal
    expectancy = round(sum(pnls) / len(pnls), 2) if pnls else 0

    # Cost-adjusted P&L (subtract API cost in ₹, approx)
    pnl_inr = sum(pnls)
    cost_inr = total_cost_usd * 83
    net_pnl  = round(pnl_inr - cost_inr, 2)

    return {
        "label":           label,
        "total_signals":   len(rows),
        "buy_signals":     len(buy_sig),
        "open_trades":     len(open_),
        "closed_trades":   len(closed),
        "wins":            len(wins),
        "losses":          len(losses),
        "win_rate_pct":    round(len(wins) / len(closed) * 100, 1) if closed else 0,
        "accuracy_pct":    round(len(correct) / len(closed) * 100, 1) if closed else 0,
        "total_pnl_inr":   round(pnl_inr, 2),
        "avg_win_inr":     round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0,
        "avg_loss_inr":    round(sum(los_pnls) / len(los_pnls), 2) if los_pnls else 0,
        "expectancy_inr":  expectancy,
        "total_cost_usd":  round(total_cost_usd, 4),
        "total_cost_inr":  round(cost_inr, 2),
        "net_pnl_inr":     net_pnl,   # P&L after deducting API cost
    }


def _ticker_breakdown(rows: list[dict]) -> dict:
    """Per-ticker accuracy breakdown."""
    by_ticker = defaultdict(list)
    for r in rows:
        if r.get("result") in ("WIN","LOSS"):
            by_ticker[r["ticker"]].append(r)

    result = {}
    for ticker, trades in by_ticker.items():
        wins = sum(1 for t in trades if t["result"] == "WIN")
        result[ticker] = {
            "total":    len(trades),
            "wins":     wins,
            "win_rate": round(wins / len(trades) * 100, 1),
        }
    return dict(sorted(result.items(), key=lambda x: -x[1]["win_rate"]))


def _date_filter(rows: list[dict], start: date, end: date) -> list[dict]:
    """Filter rows by date range."""
    filtered = []
    for r in rows:
        try:
            d = datetime.strptime(r["date"][:10], "%Y-%m-%d").date()
            if start <= d <= end:
                filtered.append(r)
        except Exception:
            pass
    return filtered


# ── Report builders ────────────────────────────────────────────────────────────

def build_daily_report(save: bool = True) -> str:
    """Build today's performance report across all 3 strategies."""
    today = date.today()
    rows_a = _date_filter(load_all("A"), today, today)
    rows_b = _date_filter(load_all("B"), today, today)
    rows_c = _date_filter(load_all("C"), today, today)

    m_a = _compute_metrics(rows_a, "Rule-Based (A)")
    m_b = _compute_metrics(rows_b, "Sonnet (B)")
    m_c = _compute_metrics(rows_c, "Opus (C)")

    lines = [
        f"",
        f"{'═'*62}",
        f"  DAILY REPORT — {today.strftime('%d %b %Y')}",
        f"{'═'*62}",
        f"",
        f"  {'METRIC':<22} {'RULE-BASED':>12} {'SONNET':>10} {'OPUS':>10}",
        f"  {'─'*56}",
        f"  {'Signals today':<22} {m_a['total_signals']:>12} {m_b['total_signals']:>10} {m_c['total_signals']:>10}",
        f"  {'BUY signals':<22} {m_a['buy_signals']:>12} {m_b['buy_signals']:>10} {m_c['buy_signals']:>10}",
        f"  {'Open trades':<22} {m_a['open_trades']:>12} {m_b['open_trades']:>10} {m_c['open_trades']:>10}",
        f"  {'Closed trades':<22} {m_a['closed_trades']:>12} {m_b['closed_trades']:>10} {m_c['closed_trades']:>10}",
        f"  {'Win rate':<22} {str(m_a['win_rate_pct'])+'%':>12} {str(m_b['win_rate_pct'])+'%':>10} {str(m_c['win_rate_pct'])+'%':>10}",
        f"  {'Accuracy':<22} {str(m_a['accuracy_pct'])+'%':>12} {str(m_b['accuracy_pct'])+'%':>10} {str(m_c['accuracy_pct'])+'%':>10}",
        f"  {'Total P&L (₹)':<22} {'₹'+str(m_a['total_pnl_inr']):>12} {'₹'+str(m_b['total_pnl_inr']):>10} {'₹'+str(m_c['total_pnl_inr']):>10}",
        f"  {'API cost today':<22} {'₹0':>12} {'₹'+str(m_b['total_cost_inr']):>10} {'₹'+str(m_c['total_cost_inr']):>10}",
        f"  {'Net P&L (after cost)':<22} {'₹'+str(m_a['net_pnl_inr']):>12} {'₹'+str(m_b['net_pnl_inr']):>10} {'₹'+str(m_c['net_pnl_inr']):>10}",
        f"  {'─'*56}",
    ]

    # Daily winner
    nets = {"Rule-Based": m_a["net_pnl_inr"], "Sonnet": m_b["net_pnl_inr"], "Opus": m_c["net_pnl_inr"]}
    winner = max(nets, key=nets.get)
    lines += [
        f"  🏆 TODAY'S WINNER: {winner}",
        f"",
        f"  ⚠  Note: Trades may still be open — final verdict after exit",
        f"{'═'*62}",
        f"",
    ]

    report = "\n".join(lines)
    print(report)

    if save:
        _save_report(f"daily_{today.strftime('%Y%m%d')}.txt", report)

    _send_telegram(f"📊 DAILY REPORT\n{_compact_daily(m_a, m_b, m_c, winner)}")
    return report


def build_weekly_report(save: bool = True) -> str:
    """Build full weekly report — deeper analysis including cost-efficiency."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday())   # Monday
    week_end   = today

    rows_a = _date_filter(load_all("A"), week_start, week_end)
    rows_b = _date_filter(load_all("B"), week_start, week_end)
    rows_c = _date_filter(load_all("C"), week_start, week_end)

    m_a = _compute_metrics(rows_a, "Rule-Based (A)")
    m_b = _compute_metrics(rows_b, "Sonnet (B)")
    m_c = _compute_metrics(rows_c, "Opus (C)")

    # Cost per correct signal
    def cost_per_win(m):
        if m["wins"] == 0:
            return "N/A"
        if m["total_cost_inr"] == 0:
            return "₹0 (free)"
        return f"₹{round(m['total_cost_inr'] / m['wins'], 1)}"

    # Opus vs Sonnet accuracy premium
    acc_premium = round(m_c["accuracy_pct"] - m_b["accuracy_pct"], 1)
    cost_premium = round(m_c["total_cost_inr"] - m_b["total_cost_inr"], 1)
    opus_justified = acc_premium > 5   # Opus worth it if 5%+ more accurate

    ticker_a = _ticker_breakdown(rows_a)
    ticker_b = _ticker_breakdown(rows_b)

    lines = [
        f"",
        f"{'═'*62}",
        f"  WEEKLY REPORT — {week_start.strftime('%d %b')} to {week_end.strftime('%d %b %Y')}",
        f"{'═'*62}",
        f"",
        f"  PERFORMANCE SUMMARY",
        f"  {'─'*56}",
        f"  {'METRIC':<28} {'RULE-BASED':>10} {'SONNET':>8} {'OPUS':>8}",
        f"  {'─'*56}",
        f"  {'Total signals':<28} {m_a['total_signals']:>10} {m_b['total_signals']:>8} {m_c['total_signals']:>8}",
        f"  {'BUY signals':<28} {m_a['buy_signals']:>10} {m_b['buy_signals']:>8} {m_c['buy_signals']:>8}",
        f"  {'Closed trades':<28} {m_a['closed_trades']:>10} {m_b['closed_trades']:>8} {m_c['closed_trades']:>8}",
        f"  {'Win rate':<28} {str(m_a['win_rate_pct'])+'%':>10} {str(m_b['win_rate_pct'])+'%':>8} {str(m_c['win_rate_pct'])+'%':>8}",
        f"  {'Accuracy (BUY+AVOID)':<28} {str(m_a['accuracy_pct'])+'%':>10} {str(m_b['accuracy_pct'])+'%':>8} {str(m_c['accuracy_pct'])+'%':>8}",
        f"  {'Avg win (₹)':<28} {'₹'+str(m_a['avg_win_inr']):>10} {'₹'+str(m_b['avg_win_inr']):>8} {'₹'+str(m_c['avg_win_inr']):>8}",
        f"  {'Avg loss (₹)':<28} {'₹'+str(m_a['avg_loss_inr']):>10} {'₹'+str(m_b['avg_loss_inr']):>8} {'₹'+str(m_c['avg_loss_inr']):>8}",
        f"  {'Expectancy per trade (₹)':<28} {'₹'+str(m_a['expectancy_inr']):>10} {'₹'+str(m_b['expectancy_inr']):>8} {'₹'+str(m_c['expectancy_inr']):>8}",
        f"  {'─'*56}",
        f"",
        f"  COST ANALYSIS",
        f"  {'─'*56}",
        f"  {'Weekly API cost':<28} {'₹0':>10} {'₹'+str(m_b['total_cost_inr']):>8} {'₹'+str(m_c['total_cost_inr']):>8}",
        f"  {'Cost per winning signal':<28} {'₹0':>10} {cost_per_win(m_b):>8} {cost_per_win(m_c):>8}",
        f"  {'Net P&L (after API cost)':<28} {'₹'+str(m_a['net_pnl_inr']):>10} {'₹'+str(m_b['net_pnl_inr']):>8} {'₹'+str(m_c['net_pnl_inr']):>8}",
        f"  {'─'*56}",
        f"",
        f"  OPUS vs SONNET ANALYSIS",
        f"  {'─'*56}",
        f"  Accuracy premium (Opus over Sonnet) : {acc_premium:+.1f}%",
        f"  Extra weekly cost of Opus           : ₹{cost_premium:.1f}",
        f"  Is Opus worth the premium?          : {'YES ✓' if opus_justified else 'NO ✗ — Sonnet is sufficient'}",
        f"",
    ]

    # Top stocks by win rate (Strategy A as baseline)
    if ticker_a:
        lines += [
            f"  TOP STOCKS THIS WEEK (Rule-Based Accuracy)",
            f"  {'─'*40}",
        ]
        for ticker, stats in list(ticker_a.items())[:5]:
            lines.append(
                f"  {ticker:20s} {stats['wins']}/{stats['total']} "
                f"trades — {stats['win_rate']}% win rate"
            )
        lines.append("")

    # Overall winner
    nets = {
        "Rule-Based": m_a["net_pnl_inr"],
        "Sonnet":     m_b["net_pnl_inr"],
        "Opus":       m_c["net_pnl_inr"],
    }
    winner = max(nets, key=nets.get)

    lines += [
        f"  {'═'*56}",
        f"  🏆 WEEK WINNER (net P&L): {winner}",
        f"  💡 RECOMMENDATION:",
    ]

    # Recommendation logic
    if m_a["net_pnl_inr"] >= m_b["net_pnl_inr"] and m_a["net_pnl_inr"] >= m_c["net_pnl_inr"]:
        lines.append(f"     Rule-Based is performing best and costs nothing.")
        lines.append(f"     Keep running all 3 — wait for more data before dropping API strategies.")
    elif opus_justified and m_c["net_pnl_inr"] > m_b["net_pnl_inr"]:
        lines.append(f"     Opus accuracy justifies cost. Consider Opus as primary.")
        lines.append(f"     Keep Rule-Based as free confirmation layer.")
    else:
        lines.append(f"     Sonnet is the best value — strong accuracy, lower cost than Opus.")
        lines.append(f"     Rule-Based as filter, Sonnet as final entry signal.")

    lines += [
        f"  {'═'*56}",
        f"  Next report: {(today + timedelta(days=1)).strftime('%d %b')} at 16:00 IST",
        f"",
    ]

    report = "\n".join(lines)
    print(report)

    if save:
        _save_report(f"weekly_{week_start.strftime('%Y%m%d')}.txt", report)

    _send_telegram(f"📋 WEEKLY REPORT\n{_compact_weekly(m_a, m_b, m_c, winner, opus_justified)}")
    return report


# ── Compact versions for Telegram ─────────────────────────────────────────────

def _compact_daily(m_a, m_b, m_c, winner) -> str:
    return (
        f"{date.today().strftime('%d %b %Y')}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"           A      B      C\n"
        f"Win rate  {m_a['win_rate_pct']}%  {m_b['win_rate_pct']}%  {m_c['win_rate_pct']}%\n"
        f"P&L(net) ₹{m_a['net_pnl_inr']}  ₹{m_b['net_pnl_inr']}  ₹{m_c['net_pnl_inr']}\n"
        f"Cost      ₹0  ₹{m_b['total_cost_inr']}  ₹{m_c['total_cost_inr']}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🏆 {winner}"
    )


def _compact_weekly(m_a, m_b, m_c, winner, opus_justified) -> str:
    return (
        f"Week ending {date.today().strftime('%d %b')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Strategy   WinRate  NetP&L   Cost\n"
        f"Rule-Based {m_a['win_rate_pct']}%    ₹{m_a['net_pnl_inr']}    ₹0\n"
        f"Sonnet     {m_b['win_rate_pct']}%    ₹{m_b['net_pnl_inr']}    ₹{m_b['total_cost_inr']}\n"
        f"Opus       {m_c['win_rate_pct']}%    ₹{m_c['net_pnl_inr']}    ₹{m_c['total_cost_inr']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 {winner}\n"
        f"Opus worth it? {'YES ✓' if opus_justified else 'NO ✗'}"
    )


# ── Utils ──────────────────────────────────────────────────────────────────────

def _save_report(filename: str, content: str):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, filename)
    with open(path, "w") as f:
        f.write(content)
    logger.info(f"Report saved: {path}")


def _send_telegram(msg: str):
    try:
        from monitor.telegram_bot import send_message
        send_message(msg)
    except Exception:
        pass


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TradeMind Reporter")
    parser.add_argument("--daily",  action="store_true", help="Print daily report now")
    parser.add_argument("--weekly", action="store_true", help="Print weekly report now")
    args = parser.parse_args()

    if args.daily:
        build_daily_report()
    elif args.weekly:
        build_weekly_report()
    else:
        # Scheduled mode
        schedule.every().day.at("16:00").do(build_daily_report)
        schedule.every().friday.at("16:15").do(build_weekly_report)

        logger.info("Reporter scheduled: Daily 16:00 | Weekly Friday 16:15")
        build_daily_report()   # immediate on startup

        while True:
            schedule.run_pending()
            time.sleep(30)
