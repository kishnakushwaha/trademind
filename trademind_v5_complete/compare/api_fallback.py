"""
compare/api_fallback.py
━━━━━━━━━━━━━━━━━━━━━━━
API Limit Fallback Manager

When Claude API is rate-limited or quota exhausted:
1. Catches the exact error
2. Logs it with timestamp
3. Sends Telegram + desktop notification immediately
4. Falls back to Strategy A (rule-based) automatically
5. Caches the original request — retries later when API recovers
6. Shows you exactly what was asked before the failure

Works for both Sonnet (B) and Opus (C).
"""

import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path

from compare.shared_logger import get_logger

logger = get_logger("APIFallback")

FALLBACK_LOG   = "logs/api_fallback_log.json"   # full history of failures
PENDING_QUEUE  = "logs/pending_retries.json"     # requests to retry when API recovers

# Anthropic error codes that mean "limit hit"
RATE_LIMIT_CODES = {
    429,           # Too Many Requests
    529,           # Overloaded
}
QUOTA_MESSAGES = [
    "rate limit",
    "quota exceeded",
    "overloaded",
    "capacity",
    "too many requests",
    "credit balance",
    "insufficient",
]


# ── Core fallback function ─────────────────────────────────────────────────────

def handle_api_failure(
    error: Exception,
    strategy_id: str,       # "B" or "C"
    ticker: str,
    original_context: str,  # the full prompt that was sent to Claude
    fallback_fn,            # callable: rule-based analysis function
    headlines: list = None,
) -> dict:
    """
    Called when a Claude API call fails.

    1. Classifies the error (rate limit vs quota vs other)
    2. Logs the failure
    3. Saves the original request to retry queue
    4. Sends notification
    5. Runs rule-based fallback and returns that result
    6. Marks result clearly as FALLBACK so reports show it correctly

    Args:
        error:            the exception caught from the API call
        strategy_id:      "B" (Sonnet) or "C" (Opus)
        ticker:           stock being analyzed
        original_context: the exact prompt that failed — saved for retry
        fallback_fn:      rule-based signal function to call instead
        headlines:        news headlines list

    Returns:
        dict: fallback signal result, with fallback_used=True flag
    """
    error_type = _classify_error(error)
    error_str  = str(error)
    model      = "Sonnet" if strategy_id == "B" else "Opus"

    logger.warning(
        f"API failure [{error_type}] for {model} on {ticker}: {error_str[:100]}"
    )

    # ── 1. Log the failure ────────────────────────────────────────────────────
    _log_failure(strategy_id, ticker, error_type, error_str, original_context)

    # ── 2. Add to retry queue ─────────────────────────────────────────────────
    _add_to_retry_queue(strategy_id, ticker, original_context)

    # ── 3. Send notification ──────────────────────────────────────────────────
    _send_failure_notification(strategy_id, ticker, error_type, error_str, model)

    # ── 4. Run rule-based fallback ────────────────────────────────────────────
    logger.info(f"Running rule-based fallback for {ticker}...")
    try:
        fallback_result = fallback_fn(ticker, headlines or [])
        if fallback_result:
            fallback_result["fallback_used"]   = True
            fallback_result["fallback_reason"] = f"{model} API {error_type}"
            fallback_result["original_prompt"] = original_context[:500]  # truncated
            logger.info(
                f"Fallback succeeded for {ticker}: "
                f"{fallback_result.get('signal')} | Score: {fallback_result.get('score')}"
            )
            return fallback_result
    except Exception as fe:
        logger.error(f"Fallback also failed for {ticker}: {fe}")

    # ── 5. Return neutral signal if even fallback fails ───────────────────────
    return {
        "signal":          "HOLD",
        "score":           0.5,
        "confidence":      "LOW",
        "fallback_used":   True,
        "fallback_reason": f"{model} API {error_type} + Rule-based error",
        "original_prompt": original_context[:500],
        "detail":          f"All analysis failed — {error_type}. Original request saved for retry.",
        "api_cost_usd":    0.0,
    }


def _classify_error(error: Exception) -> str:
    """Classify API error type."""
    error_str = str(error).lower()
    code      = getattr(error, "status_code", None)

    if code in RATE_LIMIT_CODES:
        return "RATE_LIMIT"
    if any(msg in error_str for msg in QUOTA_MESSAGES):
        if "credit" in error_str or "insufficient" in error_str:
            return "QUOTA_EXHAUSTED"
        return "RATE_LIMIT"
    return "API_ERROR"


# ── Failure logger ─────────────────────────────────────────────────────────────

def _log_failure(strategy_id: str, ticker: str, error_type: str,
                 error_str: str, original_context: str):
    """Append failure to the fallback log JSON."""
    os.makedirs("logs", exist_ok=True)

    history = []
    if os.path.exists(FALLBACK_LOG):
        try:
            with open(FALLBACK_LOG) as f:
                history = json.load(f)
        except Exception:
            history = []

    history.append({
        "timestamp":        datetime.now().isoformat(),
        "strategy":         strategy_id,
        "ticker":           ticker,
        "error_type":       error_type,
        "error_message":    error_str[:300],
        "original_context": original_context[:1000],   # what was asked
    })

    with open(FALLBACK_LOG, "w") as f:
        json.dump(history[-500:], f, indent=2)   # keep last 500 entries


# ── Retry queue ────────────────────────────────────────────────────────────────

def _add_to_retry_queue(strategy_id: str, ticker: str, context: str):
    """Save failed request for retry when API recovers."""
    queue = _load_retry_queue()
    # Avoid duplicates for same ticker+strategy
    queue = [r for r in queue if not (r["strategy"] == strategy_id and r["ticker"] == ticker)]
    queue.append({
        "strategy":   strategy_id,
        "ticker":     ticker,
        "context":    context[:1000],
        "queued_at":  datetime.now().isoformat(),
        "retries":    0,
    })
    _save_retry_queue(queue)


def retry_pending_requests(
    sonnet_fn=None,
    opus_fn=None,
    max_retries: int = 3
) -> list[dict]:
    """
    Called when API recovers — retries all pending requests.

    Args:
        sonnet_fn: function(ticker, context) for Sonnet
        opus_fn:   function(ticker, context) for Opus
        max_retries: skip items that have already failed this many times

    Returns:
        list of retry results
    """
    queue   = _load_retry_queue()
    success = []
    failed  = []

    if not queue:
        logger.info("No pending retries")
        return []

    logger.info(f"Retrying {len(queue)} pending requests...")

    for item in queue:
        if item.get("retries", 0) >= max_retries:
            logger.warning(f"Skipping {item['ticker']} — max retries reached")
            failed.append(item)
            continue

        strategy = item["strategy"]
        ticker   = item["ticker"]
        context  = item["context"]

        try:
            if strategy == "B" and sonnet_fn:
                result = sonnet_fn(ticker, context)
            elif strategy == "C" and opus_fn:
                result = opus_fn(ticker, context)
            else:
                failed.append(item)
                continue

            if result:
                logger.info(f"Retry SUCCESS: {strategy} | {ticker} | {result.get('signal')}")
                success.append({"strategy": strategy, "ticker": ticker, "result": result})
                # Notify recovery
                _send_recovery_notification(strategy, ticker, result)
            else:
                item["retries"] = item.get("retries", 0) + 1
                failed.append(item)

        except Exception as e:
            logger.error(f"Retry failed for {ticker}: {e}")
            item["retries"] = item.get("retries", 0) + 1
            failed.append(item)

        time.sleep(2)

    _save_retry_queue(failed)
    logger.info(f"Retry done: {len(success)} success, {len(failed)} still pending")
    return success


def _load_retry_queue() -> list[dict]:
    if not os.path.exists(PENDING_QUEUE):
        return []
    try:
        with open(PENDING_QUEUE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_retry_queue(queue: list[dict]):
    with open(PENDING_QUEUE, "w") as f:
        json.dump(queue, f, indent=2)


# ── Notification system ────────────────────────────────────────────────────────

def _send_failure_notification(strategy_id: str, ticker: str,
                               error_type: str, error_str: str, model: str):
    """Send alert via Telegram + desktop notification."""
    # What was happening at the time of failure
    queue_size = len(_load_retry_queue())

    if error_type == "QUOTA_EXHAUSTED":
        urgency = "🔴 QUOTA EXHAUSTED"
        action  = "Top up at console.anthropic.com to resume AI analysis"
    elif error_type == "RATE_LIMIT":
        urgency = "🟡 RATE LIMITED"
        action  = "Auto-retrying in next scan cycle. Rule-based fallback active."
    else:
        urgency = "🟠 API ERROR"
        action  = "Check API key validity. Rule-based fallback active."

    msg = (
        f"{urgency} — {model}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Ticker      : {ticker}\n"
        f"Error       : {error_str[:80]}\n"
        f"Fallback    : Rule-Based (Strategy A) activated\n"
        f"Pending Q   : {queue_size + 1} request(s) queued for retry\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Action: {action}\n"
        f"Time: {datetime.now().strftime('%d %b %Y %H:%M IST')}"
    )

    # Telegram
    try:
        from monitor.telegram_bot import send_message
        send_message(msg)
    except Exception:
        pass

    # Desktop notification (Windows/Mac/Linux)
    _desktop_notify(f"TradeMind: {urgency}", f"{model} failed on {ticker}. Fallback active.")

    # Console — always works
    print(f"\n{'!'*60}")
    print(msg)
    print(f"{'!'*60}\n")


def _send_recovery_notification(strategy_id: str, ticker: str, result: dict):
    """Notify when a pending retry succeeds."""
    model = "Sonnet" if strategy_id == "B" else "Opus"
    msg = (
        f"✅ API RECOVERED — {model}\n"
        f"Retry success: {ticker}\n"
        f"Signal: {result.get('signal')} | Score: {result.get('score')}\n"
        f"Time: {datetime.now().strftime('%H:%M IST')}"
    )
    try:
        from monitor.telegram_bot import send_message
        send_message(msg)
    except Exception:
        pass
    logger.info(msg)


def _desktop_notify(title: str, message: str):
    """Cross-platform desktop notification."""
    import platform
    system = platform.system()
    try:
        if system == "Windows":
            # Uses built-in Windows toast (no extra package)
            from ctypes import windll
            windll.user32.MessageBoxW(0, message, title, 0x40 | 0x1000)
        elif system == "Darwin":   # macOS
            os.system(f"osascript -e 'display notification \"{message}\" with title \"{title}\"'")
        elif system == "Linux":
            os.system(f'notify-send "{title}" "{message}"')
    except Exception as e:
        logger.debug(f"Desktop notification failed (non-critical): {e}")


# ── Status viewer ──────────────────────────────────────────────────────────────

def show_failure_history(last_n: int = 20):
    """
    Print the last N API failures with what was originally requested.
    Call this to see exactly what was asked before the failure.
    """
    if not os.path.exists(FALLBACK_LOG):
        print("No API failures logged yet.")
        return

    with open(FALLBACK_LOG) as f:
        history = json.load(f)

    recent = history[-last_n:]
    print(f"\n{'═'*60}")
    print(f"  API FAILURE HISTORY — Last {len(recent)} entries")
    print(f"{'═'*60}")

    for i, entry in enumerate(reversed(recent), 1):
        print(f"\n  [{i}] {entry['timestamp'][:16]} | "
              f"Strategy {entry['strategy']} | "
              f"{entry['ticker']} | "
              f"{entry['error_type']}")
        print(f"       Error: {entry['error_message'][:80]}")
        print(f"       What was asked:")
        # Show the original context — trimmed for readability
        ctx_lines = entry.get("original_context", "")[:400].split("\n")
        for line in ctx_lines[:8]:
            if line.strip():
                print(f"         {line}")
        if len(ctx_lines) > 8:
            print(f"         ... (truncated — full context in {FALLBACK_LOG})")

    pending = _load_retry_queue()
    print(f"\n  Pending retries: {len(pending)} request(s) waiting")
    print(f"{'═'*60}\n")


def show_pending_retries():
    """Show all requests waiting to be retried."""
    queue = _load_retry_queue()
    if not queue:
        print("No pending retries.")
        return

    print(f"\n{'═'*60}")
    print(f"  PENDING RETRY QUEUE — {len(queue)} item(s)")
    print(f"{'═'*60}")
    for item in queue:
        print(f"\n  Strategy {item['strategy']} | {item['ticker']}")
        print(f"  Queued: {item['queued_at'][:16]} | Retries: {item.get('retries',0)}")
        print(f"  Context preview:")
        for line in item.get("context","")[:200].split("\n")[:4]:
            if line.strip():
                print(f"    {line}")
    print(f"{'═'*60}\n")


# ── API health check ───────────────────────────────────────────────────────────

def check_api_health(model: str = "claude-sonnet-4-20250514") -> dict:
    """
    Quick health check — sends a minimal API call to verify the API is working.
    Call this before starting a scan or after a failure.

    Returns:
        {"healthy": bool, "latency_ms": float, "error": str|None}
    """
    import anthropic as ant
    client = ant.Anthropic()
    start  = time.time()

    try:
        resp = client.messages.create(
            model      = model,
            max_tokens = 10,
            messages   = [{"role": "user", "content": "Reply: OK"}]
        )
        latency = round((time.time() - start) * 1000, 0)
        logger.info(f"API health check OK | Model: {model} | Latency: {latency}ms")
        return {"healthy": True, "latency_ms": latency, "error": None}

    except Exception as e:
        error_type = _classify_error(e)
        logger.warning(f"API health check FAILED | {error_type}: {e}")
        return {"healthy": False, "latency_ms": None, "error": str(e),
                "error_type": error_type}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="API Fallback Manager")
    parser.add_argument("--history",  action="store_true", help="Show failure history")
    parser.add_argument("--pending",  action="store_true", help="Show pending retries")
    parser.add_argument("--health",   action="store_true", help="Check API health")
    parser.add_argument("--last",     type=int, default=20, help="Last N entries to show")
    args = parser.parse_args()

    if args.history:
        show_failure_history(args.last)
    elif args.pending:
        show_pending_retries()
    elif args.health:
        result = check_api_health()
        status = "✅ HEALTHY" if result["healthy"] else "❌ DOWN"
        print(f"\nAPI Status: {status}")
        if result["healthy"]:
            print(f"Latency: {result['latency_ms']}ms")
        else:
            print(f"Error: {result['error']}")
    else:
        show_failure_history(args.last)
        show_pending_retries()
