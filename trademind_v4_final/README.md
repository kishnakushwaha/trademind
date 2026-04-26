# TradeMind AI Trading Agent

AI-powered stock market agent for NSE/BSE (Indian markets).
Built for systematic swing trading with strict risk management.

## Architecture

```
Data (yfinance + news) → Signal Engine (Technical + Sentiment + Volume)
→ Risk Filter (position sizing, drawdown limits)
→ Paper Trader (Phase 3) / Kite API (Phase 4)
→ Telegram Alerts
```

## Setup

```bash
# 1. Clone / unzip project
cd trademind

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — set TOTAL_CAPITAL, leave Kite blank for now

# 5. Create logs directory
mkdir logs
```

## Usage

```bash
# Backtest strategy on RELIANCE (do this first)
python main.py --mode backtest --ticker RELIANCE.NS --capital 10000

# Backtest multiple stocks
python main.py --mode backtest --ticker TCS.NS
python main.py --mode backtest --ticker HDFCBANK.NS

# Run a single scan (test signal engine)
python main.py --mode scan

# Start full paper trading agent (scheduled)
python main.py --mode agent

# Check your paper trading performance
python main.py --mode performance
```

## Phase Roadmap

| Phase | Action | When to move on |
|-------|--------|-----------------|
| 1 | Run backtest on 5+ stocks | Win rate >= 55%, positive expectancy |
| 2 | Paper trade for 60+ days | Live performance matches backtest |
| 3 | Add Telegram alerts | Working reliably |
| 4 | Kite API live trading | Only after Phase 2 complete |

## Risk Rules (hardcoded — do not change)

- Max 5% of capital at risk per trade
- Stop loss max 7% from entry
- Minimum 1:2 reward-to-risk ratio
- Max 2 open positions at once
- Daily loss limit: 3% of capital
- System halts at 15% drawdown

## Key Files

```
config/settings.py      ← All configuration (risk params, thresholds)
config/watchlist.py     ← Stocks to scan
data/fetcher.py         ← Price and news data
signals/technical.py    ← EMA, RSI, MACD, Volume signals
signals/sentiment.py    ← FinBERT news sentiment
signals/combiner.py     ← Weighted signal combination
risk/position_sizer.py  ← Position size calculator
backtest/engine.py      ← Historical strategy testing
agent/brain.py          ← Main decision engine
agent/paper_trader.py   ← Paper trading simulation
agent/live_trader.py    ← Zerodha Kite API (Phase 4)
monitor/telegram_bot.py ← Trade alerts
main.py                 ← Entry point
```

## Libraries Used

- `yfinance` — NSE/BSE historical and live price data
- `pandas-ta` — Technical indicators (EMA, RSI, MACD, Bollinger Bands)
- `transformers` + `ProsusAI/finbert` — Financial news sentiment
- `kiteconnect` — Zerodha API for live trading
- `python-telegram-bot` — Trade alerts
- `schedule` — Job scheduling

## Important Notes

1. This system does NOT guarantee profits. No system does.
2. Always backtest before paper trading. Always paper trade before live.
3. The risk module is the most important module — never disable it.
4. Start with ₹3,000 paper capital. Add real capital only when proven.
