"""
risk/position_sizer.py
Calculates position size, stop loss, targets, and validates trade risk.
THIS MODULE IS NON-NEGOTIABLE. Every trade must pass through here.
"""

import logging
from config.settings import (
    TOTAL_CAPITAL, RISK_PER_TRADE_PCT, MAX_SL_PCT,
    MIN_RR_RATIO, MAX_OPEN_TRADES, MAX_DAILY_LOSS_PCT
)

logger = logging.getLogger(__name__)


def calculate_position(entry_price: float, stop_loss_price: float,
                       available_capital: float = None,
                       current_open_trades: int = 0) -> dict:
    """
    Calculate position size using fixed fractional risk management.

    Rules enforced:
    - Never risk more than RISK_PER_TRADE_PCT% of capital on one trade
    - Stop loss must be within MAX_SL_PCT% of entry
    - At least MIN_RR_RATIO reward-to-risk required

    Args:
        entry_price:          planned entry price
        stop_loss_price:      planned stop loss (must be below entry for BUY)
        available_capital:    current usable capital (defaults to TOTAL_CAPITAL)
        current_open_trades:  how many positions currently open

    Returns:
        {
            "valid":           bool — True if trade passes all checks
            "qty":             int — shares to buy
            "risk_amount":     float — ₹ at risk
            "target_1":        float — 1:2 R:R target
            "target_2":        float — 1:3 R:R target
            "sl_pct":          float — SL % distance
            "rr_ratio":        float — actual R:R at target_1
            "capital_used":    float — ₹ deployed
            "pct_of_capital":  float — % of total capital deployed
            "rejection":       str | None — reason if invalid
        }
    """
    capital = available_capital or TOTAL_CAPITAL
    rejection = None

    # ── Validations ───────────────────────────────────────────────────────────
    if entry_price <= 0 or stop_loss_price <= 0:
        return {"valid": False, "rejection": "Invalid prices (must be > 0)", "qty": 0}

    if stop_loss_price >= entry_price:
        return {"valid": False, "rejection": "Stop loss must be BELOW entry price for a BUY trade", "qty": 0}

    sl_distance = entry_price - stop_loss_price
    sl_pct      = (sl_distance / entry_price) * 100

    if sl_pct > MAX_SL_PCT + 0.01:  # small tolerance for floating point
        rejection = (f"Stop loss too wide: {sl_pct:.1f}% > max {MAX_SL_PCT}%. "
                     f"Find a tighter entry or skip this trade.")

    if current_open_trades >= MAX_OPEN_TRADES:
        rejection = (f"Already have {current_open_trades} open trades. "
                     f"Max is {MAX_OPEN_TRADES}. Wait for a trade to close.")

    # ── Position sizing ───────────────────────────────────────────────────────
    risk_amount = capital * (RISK_PER_TRADE_PCT / 100)   # ₹ allowed to lose
    qty         = int(risk_amount / sl_distance)          # shares to buy

    if qty < 1:
        rejection = (f"Capital too small: ₹{capital} with ₹{sl_distance:.2f} SL "
                     f"distance gives qty=0. Widen SL slightly or add capital.")

    capital_used   = qty * entry_price
    pct_of_capital = (capital_used / capital) * 100

    # ── Targets ───────────────────────────────────────────────────────────────
    target_1 = round(entry_price + (sl_distance * 2), 2)   # 1:2 R:R
    target_2 = round(entry_price + (sl_distance * 3), 2)   # 1:3 R:R

    rr_ratio = 2.0   # by construction
    if rr_ratio < MIN_RR_RATIO and rejection is None:
        rejection = f"R:R {rr_ratio:.1f} below minimum {MIN_RR_RATIO}. Adjust targets."

    valid = rejection is None

    result = {
        "valid":          valid,
        "qty":            qty,
        "risk_amount":    round(risk_amount, 2),
        "capital_used":   round(capital_used, 2),
        "pct_of_capital": round(pct_of_capital, 1),
        "sl_pct":         round(sl_pct, 2),
        "sl_distance":    round(sl_distance, 2),
        "target_1":       target_1,
        "target_2":       target_2,
        "rr_ratio":       rr_ratio,
        "rejection":      rejection,
    }

    if valid:
        logger.info(
            f"Position approved: qty={qty}, entry=₹{entry_price}, "
            f"SL=₹{stop_loss_price} ({sl_pct:.1f}%), "
            f"T1=₹{target_1}, capital=₹{capital_used:.0f}"
        )
    else:
        logger.warning(f"Position REJECTED: {rejection}")

    return result


def check_daily_loss_limit(daily_pnl: float, capital: float = None) -> bool:
    """
    Returns True if daily loss is within limit.
    Returns False (halt trading) if daily loss exceeds MAX_DAILY_LOSS_PCT.
    """
    cap = capital or TOTAL_CAPITAL
    max_loss = cap * (MAX_DAILY_LOSS_PCT / 100)

    if daily_pnl < -max_loss:
        logger.critical(
            f"DAILY LOSS LIMIT HIT: ₹{abs(daily_pnl):.0f} > ₹{max_loss:.0f}. "
            f"Trading HALTED for today."
        )
        return False
    return True


def check_drawdown_limit(peak_capital: float, current_capital: float) -> bool:
    """
    Returns True if drawdown is within limit.
    Returns False (halt system) if drawdown exceeds MAX_DRAWDOWN_PCT.
    """
    from config.settings import MAX_DRAWDOWN_PCT
    drawdown_pct = ((peak_capital - current_capital) / peak_capital) * 100

    if drawdown_pct > MAX_DRAWDOWN_PCT:
        logger.critical(
            f"MAX DRAWDOWN HIT: {drawdown_pct:.1f}% > {MAX_DRAWDOWN_PCT}%. "
            f"System HALTED. Review strategy before resuming."
        )
        return False
    return True
