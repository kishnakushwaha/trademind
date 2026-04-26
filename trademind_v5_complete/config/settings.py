"""
config/settings.py
All configuration in one place. Edit this file before running anything.
Copy .env.example to .env and fill in your actual keys.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Zerodha Kite (Phase 4 only — leave blank until then) ──────────────────────
KITE_API_KEY    = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
KITE_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "")   # refreshed daily

# ── Telegram alerts ────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Risk management (NON-NEGOTIABLE — do not loosen these) ────────────────────
TOTAL_CAPITAL        = float(os.getenv("TOTAL_CAPITAL", "3000"))   # ₹ starting capital
RISK_PER_TRADE_PCT   = 5.0        # max 5% of capital at risk per trade
MAX_SL_PCT           = 7.0        # stop loss never wider than 7% below entry
MIN_RR_RATIO         = 2.0        # minimum reward:risk required to take trade
MAX_OPEN_TRADES      = 2          # never hold more than 2 positions simultaneously
MAX_DAILY_LOSS_PCT   = 3.0        # halt trading if daily loss exceeds 3% of capital
MAX_DRAWDOWN_PCT     = 15.0       # halt entire system if drawdown > 15%

# ── Signal thresholds ──────────────────────────────────────────────────────────
RSI_OVERSOLD         = 35
RSI_OVERBOUGHT       = 65
RSI_NEUTRAL_LOW      = 40
RSI_NEUTRAL_HIGH     = 60
EMA_FAST             = 20
EMA_SLOW             = 50
VOLUME_MULTIPLIER    = 1.5        # entry volume must be 1.5x 20-day avg

# ── Signal weights (must sum to 1.0) ──────────────────────────────────────────
WEIGHT_TECHNICAL     = 0.50
WEIGHT_SENTIMENT     = 0.30
WEIGHT_VOLUME        = 0.20

# ── Signal score threshold to generate a trade signal ─────────────────────────
BUY_SCORE_THRESHOLD  = 0.65       # score >= 0.65 → BUY signal
SELL_SCORE_THRESHOLD = 0.35       # score <= 0.35 → SELL/AVOID signal

# ── Data ───────────────────────────────────────────────────────────────────────
HISTORICAL_PERIOD    = "2y"       # 2 years of daily data for backtesting
DATA_INTERVAL        = "1d"       # daily candles (change to "1h" for intraday)
NSE_SUFFIX           = ".NS"      # yfinance NSE suffix
BSE_SUFFIX           = ".BO"      # yfinance BSE suffix

# ── Paths ──────────────────────────────────────────────────────────────────────
TRADE_LOG_PATH       = "logs/trades.csv"
BACKTEST_RESULTS_PATH = "logs/backtest_results.csv"

# ── Mode ───────────────────────────────────────────────────────────────────────
# "paper"  → simulate trades, no real orders (Phase 3)
# "live"   → real orders via Kite API (Phase 4 — validate paper first)
TRADING_MODE = os.getenv("TRADING_MODE", "paper")
