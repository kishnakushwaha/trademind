"""
config/watchlist.py
Stocks the agent monitors. Only mid/large cap with strong fundamentals.
Research each stock on Screener.in before adding it here.
"""

# Format: "TICKER.NS" for NSE, "TICKER.BO" for BSE
WATCHLIST = [
    # Large cap — lower risk, lower reward
    "RELIANCE.NS",
    "TCS.NS",
    "INFY.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "AXISBANK.NS",
    "WIPRO.NS",
    "BHARTIARTL.NS",
    "NESTLEIND.NS",
    "TITAN.NS",

    # Mid cap — higher reward, need tighter stop loss
    "PERSISTENT.NS",
    "COFORGE.NS",
    "TATAELXSI.NS",
    "LTIM.NS",
    "POLYCAB.NS",
    "ABCAPITAL.NS",
    "NMDC.NS",
    "IRCTC.NS",
]

# Sectors mapped to tickers (for sector rotation analysis)
SECTORS = {
    "IT":      ["TCS.NS", "INFY.NS", "WIPRO.NS", "PERSISTENT.NS", "COFORGE.NS"],
    "Banking": ["HDFCBANK.NS", "ICICIBANK.NS", "AXISBANK.NS"],
    "Telecom": ["BHARTIARTL.NS"],
    "FMCG":    ["NESTLEIND.NS"],
    "Infra":   ["POLYCAB.NS"],
    "Energy":  ["RELIANCE.NS"],
}
