"""
compare/strategy_comparator.py

Runs BOTH approaches in parallel on the same stocks at the same time:
  - Strategy A: Rule-based (EMA + RSI + Volume + Keyword sentiment)
  - Strategy B: Claude AI brain (actual LLM analysis)

Tracks every signal and outcome independently.
After N trades you get hard data on which is better.

Run:
    python -m compare.strategy_comparator --once    # single cycle
    python -m compare.strategy_comparator           # scheduled daily
"""

import csv
import json
import os
import time
import logging
from datetime import datetime, date

import yfinance as yf

from config.settings import TOTAL_CAPITAL, BUY_SCORE_THRESHOLD
from config.watchlist import WATCHLIST
from data.fetcher import fetch_ohlcv, fetch_news_headlines, filter_headlines_for_ticker
from signals.technical import compute_indicators, score_technical
from signals.sentiment import score_sentiment_keywords
from signals.combiner import combine_signals
from risk.position_sizer import calculate_position

logger = logging.getLogger(__name__)

COMPARISON_LOG = "logs/strategy_comparison.csv"
METRICS_LOG    = "logs/strategy_metrics.json"

HEADERS = [
    "date", "ticker",
    # Strategy A — Rule-based
    "a_signal", "a_score", "a_tech_score", "a_sent_score", "a_vol_ratio",
    "a_rsi", "a_trend",
    # Strategy B — Claude AI
    "b_signal", "b_score", "b_confidence", "b_reasoning",
    # Agreement
    "agree",
    # Outcome (filled in later when trade closes)
    "entry_price", "sl_price", "target_1",
    "exit_price", "exit_date", "pnl_if_a_took", "pnl_if_b_took",
    "a_correct", "b_correct",
]


class StrategyComparator:
    """
    Runs both signal engines on the same data simultaneously.
    Tracks which one was right after the trade resolves.
    """

    def __init__(self):
        self._ensure_log()
        logger.info("StrategyComparator initialized — running A vs B in parallel")

    # ── Main comparison scan ───────────────────────────────────────────────────

    def run_comparison(self):
        """Run both strategies on all watchlist stocks and log results."""
        logger.info(f"=== A vs B COMPARISON SCAN — {datetime.now().strftime('%d %b %Y %H:%M')} ===")

        headlines = fetch_news_headlines(max_articles=100)
        results   = []

        for ticker in WATCHLIST:
            logger.info(f"Comparing: {ticker}")

            # ── Strategy A: Rule-based ─────────────────────────────────────
            a_result = self._run_strategy_a(ticker, headlines)

            # ── Strategy B: Claude AI ──────────────────────────────────────
            b_result = self._run_strategy_b(ticker, headlines)

            if a_result is None or b_result is None:
                continue

            # ── Do they agree? ─────────────────────────────────────────────
            agree = a_result["signal"] == b_result["signal"]

            # ── Get current price for reference ───────────────────────────
            price = self._live_price(ticker)
            sl    = round(price * 0.93, 2) if price else None
            t1    = round(price + (price - sl) * 2, 2) if (price and sl) else None

            row = {
                "date":         datetime.now().strftime("%Y-%m-%d %H:%M"),
                "ticker":       ticker,
                # A
                "a_signal":     a_result["signal"],
                "a_score":      a_result["score"],
                "a_tech_score": a_result.get("tech_score", ""),
                "a_sent_score": a_result.get("sent_score", ""),
                "a_vol_ratio":  a_result.get("vol_ratio", ""),
                "a_rsi":        a_result.get("rsi", ""),
                "a_trend":      a_result.get("trend", ""),
                # B
                "b_signal":     b_result["signal"],
                "b_score":      b_result["score"],
                "b_confidence": b_result.get("confidence", ""),
                "b_reasoning":  b_result.get("reasoning", ""),
                # Agreement
                "agree":        "YES" if agree else "NO",
                # Outcome — filled later
                "entry_price":  price or "",
                "sl_price":     sl or "",
                "target_1":     t1 or "",
                "exit_price":   "",
                "exit_date":    "",
                "pnl_if_a_took": "",
                "pnl_if_b_took": "",
                "a_correct":    "",
                "b_correct":    "",
            }

            self._log_row(row)
            results.append(row)

            self._print_comparison(ticker, a_result, b_result, agree)
            time.sleep(2)   # rate limit between Claude calls

        self._print_summary(results)
        return results

    # ── Strategy A: Rule-based ─────────────────────────────────────────────────

    def _run_strategy_a(self, ticker: str, headlines: list) -> dict | None:
        """Rule-based signal: EMA + RSI + MACD + Volume + Keyword sentiment."""
        try:
            df = fetch_ohlcv(ticker, period="6mo")
            if df.empty or len(df) < 60:
                return None

            df       = compute_indicators(df)
            latest   = df.iloc[-1]
            vol_ratio = float(latest.get("Volume_ratio", 1.0))

            tech = score_technical(df)
            rel  = filter_headlines_for_ticker(headlines, ticker)
            sent = score_sentiment_keywords(rel)
            comb = combine_signals(tech, sent, vol_ratio)

            return {
                "signal":     comb["signal"],
                "score":      comb["final_score"],
                "tech_score": comb["breakdown"]["technical_score"],
                "sent_score": comb["breakdown"]["sentiment_score"],
                "vol_ratio":  round(vol_ratio, 2),
                "rsi":        tech.get("rsi"),
                "trend":      tech.get("trend"),
                "reasons":    tech.get("reasons", []),
            }
        except Exception as e:
            logger.error(f"Strategy A failed for {ticker}: {e}")
            return None

    # ── Strategy B: Claude AI ──────────────────────────────────────────────────

    def _run_strategy_b(self, ticker: str, headlines: list) -> dict | None:
        """Claude AI signal: full LLM analysis of market data."""
        try:
            from agent.claude_brain import get_claude_decision_sync
            decision = get_claude_decision_sync(ticker, headlines)

            reasoning = decision.get("reasoning", {})
            reason_str = " | ".join(f"{k}: {v}" for k, v in reasoning.items())

            return {
                "signal":     decision.get("decision", "HOLD"),
                "score":      decision.get("score", 0.5),
                "confidence": decision.get("confidence", "LOW"),
                "reasoning":  reason_str[:200],   # truncate for CSV
                "entry_zone": decision.get("entry_zone", {}),
                "stop_loss":  decision.get("stop_loss"),
                "red_flags":  decision.get("red_flags", []),
            }
        except Exception as e:
            logger.error(f"Strategy B failed for {ticker}: {e}")
            # Return neutral if Claude API unavailable
            return {
                "signal": "HOLD", "score": 0.5,
                "confidence": "LOW", "reasoning": f"API error: {e}"
            }

    # ── Outcome tracking ───────────────────────────────────────────────────────

    def update_outcome(self, ticker: str, signal_date: str,
                       exit_price: float, exit_date: str):
        """
        Call this when a trade resolves (SL or target hit).
        Updates the comparison log with actual outcome.

        Args:
            ticker:      stock symbol
            signal_date: date string matching the logged row
            exit_price:  actual exit price
            exit_date:   date of exit
        """
        rows = self._load_all_rows()
        updated = False

        for row in rows:
            if row["ticker"] == ticker and signal_date in row["date"] and not row["exit_price"]:
                entry = float(row["entry_price"]) if row["entry_price"] else 0
                sl    = float(row["sl_price"])    if row["sl_price"]    else 0
                t1    = float(row["target_1"])    if row["target_1"]    else 0

                if entry <= 0:
                    continue

                # Simulate what P&L would be for each strategy
                # A took the trade only if a_signal == BUY
                # B took the trade only if b_signal == BUY
                pnl = round(exit_price - entry, 2)

                pnl_a = pnl if row["a_signal"] == "BUY" else 0
                pnl_b = pnl if row["b_signal"] == "BUY" else 0

                # Correct = either caught the win or correctly avoided the loss
                a_correct = (
                    (row["a_signal"] == "BUY" and exit_price >= t1) or
                    (row["a_signal"] != "BUY" and exit_price <= sl)
                )
                b_correct = (
                    (row["b_signal"] == "BUY" and exit_price >= t1) or
                    (row["b_signal"] != "BUY" and exit_price <= sl)
                )

                row["exit_price"]    = exit_price
                row["exit_date"]     = exit_date
                row["pnl_if_a_took"] = pnl_a
                row["pnl_if_b_took"] = pnl_b
                row["a_correct"]     = "YES" if a_correct else "NO"
                row["b_correct"]     = "YES" if b_correct else "NO"
                updated = True
                break

        if updated:
            self._save_all_rows(rows)
            logger.info(f"Outcome updated for {ticker} on {signal_date}")
        else:
            logger.warning(f"No matching open row found for {ticker} {signal_date}")

    # ── Metrics report ─────────────────────────────────────────────────────────

    def generate_metrics_report(self) -> dict:
        """
        Calculate accuracy and P&L metrics for both strategies.
        Call this after enough trades have resolved (20+ recommended).
        """
        rows = self._load_all_rows()
        closed = [r for r in rows if r.get("exit_price")]

        if not closed:
            print("No closed trades yet. Let the agent run and resolve some trades first.")
            return {}

        def calc_metrics(signal_key, pnl_key, correct_key):
            signals  = [r for r in closed if r[signal_key] == "BUY"]
            correct  = [r for r in closed if r[correct_key] == "YES"]
            pnls     = [float(r[pnl_key]) for r in closed if r[pnl_key] not in ("", "0")]
            wins     = [p for p in pnls if p > 0]
            losses   = [p for p in pnls if p < 0]

            return {
                "total_signals":    len(signals),
                "accuracy_pct":     round(len(correct) / len(closed) * 100, 1) if closed else 0,
                "total_pnl":        round(sum(pnls), 2),
                "win_rate_pct":     round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
                "avg_win":          round(sum(wins) / len(wins), 2) if wins else 0,
                "avg_loss":         round(sum(losses) / len(losses), 2) if losses else 0,
                "expectancy":       round((sum(wins) + sum(losses)) / len(pnls), 2) if pnls else 0,
            }

        a_metrics = calc_metrics("a_signal", "pnl_if_a_took", "a_correct")
        b_metrics = calc_metrics("b_signal", "pnl_if_b_took", "b_correct")

        # Agreement stats
        agree_rows    = [r for r in closed if r["agree"] == "YES"]
        disagree_rows = [r for r in closed if r["agree"] == "NO"]

        report = {
            "generated_at":     datetime.now().isoformat(),
            "total_comparisons": len(closed),
            "agreement_rate_pct": round(len(agree_rows) / len(closed) * 100, 1) if closed else 0,
            "strategy_a_rule_based": a_metrics,
            "strategy_b_claude_ai":  b_metrics,
            "winner": (
                "CLAUDE AI" if b_metrics["total_pnl"] > a_metrics["total_pnl"]
                else "RULE-BASED" if a_metrics["total_pnl"] > b_metrics["total_pnl"]
                else "TIE"
            ),
            "recommendation": self._recommendation(a_metrics, b_metrics),
        }

        self._save_metrics(report)
        self._print_report(report)
        return report

    def _recommendation(self, a: dict, b: dict) -> str:
        if b["total_pnl"] > a["total_pnl"] and b["win_rate_pct"] > a["win_rate_pct"]:
            return "Use Claude AI exclusively — higher P&L and win rate"
        elif a["total_pnl"] > b["total_pnl"] and a["win_rate_pct"] > b["win_rate_pct"]:
            return "Use Rule-based exclusively — better performance, zero API cost"
        elif b["win_rate_pct"] > a["win_rate_pct"] and a["total_pnl"] > b["total_pnl"]:
            return "Hybrid: use Claude for entry confirmation, rule-based for exits"
        elif a["accuracy_pct"] > b["accuracy_pct"] and b["total_pnl"] > a["total_pnl"]:
            return "Hybrid: rule-based filters, Claude for final entry decision"
        else:
            return "Insufficient data — run 20+ more comparisons before deciding"

    # ── Print helpers ──────────────────────────────────────────────────────────

    def _print_comparison(self, ticker, a, b, agree):
        agree_str = "✓ AGREE" if agree else "✗ DIFFER"
        print(f"\n  {ticker}")
        print(f"    A (Rule):   {a['signal']:5s} | Score: {a['score']:.3f} | RSI: {a.get('rsi','?')} | Trend: {a.get('trend','?')}")
        print(f"    B (Claude): {b['signal']:5s} | Score: {b['score']:.3f} | {b.get('confidence','?')} confidence")
        print(f"    {agree_str}")

    def _print_summary(self, results):
        buy_a   = sum(1 for r in results if r["a_signal"] == "BUY")
        buy_b   = sum(1 for r in results if r["b_signal"] == "BUY")
        agree   = sum(1 for r in results if r["agree"] == "YES")
        print(f"\n{'='*50}")
        print(f"SCAN SUMMARY")
        print(f"  Stocks scanned  : {len(results)}")
        print(f"  A BUY signals   : {buy_a}")
        print(f"  B BUY signals   : {buy_b}")
        print(f"  Agreement       : {agree}/{len(results)} ({round(agree/len(results)*100) if results else 0}%)")
        print(f"{'='*50}\n")

    def _print_report(self, report):
        a = report["strategy_a_rule_based"]
        b = report["strategy_b_claude_ai"]
        print(f"\n{'='*60}")
        print(f"STRATEGY COMPARISON REPORT")
        print(f"{'='*60}")
        print(f"Total comparisons : {report['total_comparisons']}")
        print(f"Agreement rate    : {report['agreement_rate_pct']}%")
        print(f"\n{'':20s} {'RULE-BASED A':>15s} {'CLAUDE AI B':>15s}")
        print(f"{'Win rate':20s} {str(a['win_rate_pct'])+'%':>15s} {str(b['win_rate_pct'])+'%':>15s}")
        print(f"{'Accuracy':20s} {str(a['accuracy_pct'])+'%':>15s} {str(b['accuracy_pct'])+'%':>15s}")
        print(f"{'Total P&L':20s} {'₹'+str(a['total_pnl']):>15s} {'₹'+str(b['total_pnl']):>15s}")
        print(f"{'Avg win':20s} {'₹'+str(a['avg_win']):>15s} {'₹'+str(b['avg_win']):>15s}")
        print(f"{'Avg loss':20s} {'₹'+str(a['avg_loss']):>15s} {'₹'+str(b['avg_loss']):>15s}")
        print(f"{'Expectancy':20s} {'₹'+str(a['expectancy']):>15s} {'₹'+str(b['expectancy']):>15s}")
        print(f"\n🏆 WINNER: {report['winner']}")
        print(f"💡 RECOMMENDATION: {report['recommendation']}")
        print(f"{'='*60}\n")

    # ── CSV helpers ────────────────────────────────────────────────────────────

    def _ensure_log(self):
        os.makedirs("logs", exist_ok=True)
        if not os.path.exists(COMPARISON_LOG):
            with open(COMPARISON_LOG, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=HEADERS).writeheader()

    def _log_row(self, row: dict):
        with open(COMPARISON_LOG, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=HEADERS).writerow(row)

    def _load_all_rows(self) -> list[dict]:
        with open(COMPARISON_LOG, "r") as f:
            return list(csv.DictReader(f))

    def _save_all_rows(self, rows: list[dict]):
        with open(COMPARISON_LOG, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HEADERS)
            w.writeheader()
            w.writerows(rows)

    def _save_metrics(self, report: dict):
        with open(METRICS_LOG, "w") as f:
            json.dump(report, f, indent=2)
        logger.info(f"Metrics saved to {METRICS_LOG}")

    def _live_price(self, ticker: str) -> float | None:
        try:
            df = yf.download(ticker, period="1d", interval="5m",
                             progress=False, auto_adjust=True)
            return float(df["Close"].iloc[-1]) if not df.empty else None
        except Exception:
            return None


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import schedule
    import colorlog

    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        log_colors={"DEBUG":"cyan","INFO":"green","WARNING":"yellow",
                    "ERROR":"red","CRITICAL":"bold_red"}
    ))
    logging.basicConfig(level=logging.INFO, handlers=[handler])

    parser = argparse.ArgumentParser(description="Strategy A vs B Comparator")
    parser.add_argument("--once",   action="store_true", help="Run one comparison and exit")
    parser.add_argument("--report", action="store_true", help="Print current metrics report")
    args = parser.parse_args()

    comp = StrategyComparator()

    if args.report:
        comp.generate_metrics_report()

    elif args.once:
        comp.run_comparison()

    else:
        # Scheduled: compare every morning, report every Friday EOD
        schedule.every().day.at("09:45").do(comp.run_comparison)
        schedule.every().friday.at("15:50").do(comp.generate_metrics_report)

        print("Comparator scheduled: 09:45 daily | Report: Friday 15:50")
        comp.run_comparison()   # immediate run on startup

        while True:
            schedule.run_pending()
            time.sleep(30)
