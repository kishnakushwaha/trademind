"""
config/watchlist.py
Stocks the agent monitors. Covers Nifty 50, Next 50, and top Midcap stocks.
This gives broad market coverage to catch hidden gems while staying liquid.
"""

# ── NIFTY 50 ───────────────────────────────────────────────────────────────────
NIFTY_50 = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "BHARTIARTL.NS", "SBIN.NS", "ITC.NS", "LICI.NS", "LT.NS",
    "HCLTECH.NS", "AXISBANK.NS", "KOTAKBANK.NS", "TITAN.NS", "BAJFINANCE.NS",
    "MARUTI.NS", "SUNPHARMA.NS", "ONGC.NS", "NTPC.NS", "TATAMOTORS.NS",
    "ADANIENT.NS", "ADANIPORTS.NS", "WIPRO.NS", "NESTLEIND.NS", "ULTRACEMCO.NS",
    "COALINDIA.NS", "POWERGRID.NS", "BAJAJFINSV.NS", "TATASTEEL.NS", "JSWSTEEL.NS",
    "M&M.NS", "HINDALCO.NS", "INDUSINDBK.NS", "HDFCLIFE.NS", "SBILIFE.NS",
    "TECHM.NS", "GRASIM.NS", "DRREDDY.NS", "CIPLA.NS", "TATACONSUM.NS",
    "APOLLOHOSP.NS", "DIVISLAB.NS", "BRITANNIA.NS", "BPCL.NS", "EICHERMOT.NS",
    "HEROMOTOCO.NS", "BAJAJ-AUTO.NS", "SHRIRAMFIN.NS", "TRENT.NS", "HINDUNILVR.NS",
]

# ── NIFTY NEXT 50 ──────────────────────────────────────────────────────────────
NIFTY_NEXT_50 = [
    "ADANIGREEN.NS", "ATGL.NS", "AMBUJACEM.NS", "DMART.NS", "BANKBARODA.NS",
    "BEL.NS", "BERGEPAINT.NS", "CANBK.NS", "CHOLAFIN.NS", "COLPAL.NS",
    "DLF.NS", "DABUR.NS", "GAIL.NS", "GODREJCP.NS", "HAL.NS",
    "HAVELLS.NS", "HINDPETRO.NS", "IOC.NS", "ICICIPRULI.NS", "INDUSTOWER.NS",
    "IRCTC.NS", "JIOFIN.NS", "LICI.NS", "LODHA.NS", "MARICO.NS",
    "NHPC.NS", "NMDC.NS", "PFC.NS", "PIDILITIND.NS", "PNB.NS",
    "RECLTD.NS", "SBICARD.NS", "SIEMENS.NS", "TATAPOWER.NS", "TORNTPHARM.NS",
    "TVSMOTOR.NS", "VEDL.NS", "ZOMATO.NS", "ZYDUSLIFE.NS", "ABB.NS",
]

# ── MIDCAP GEMS (High reward potential) ────────────────────────────────────────
MIDCAP_GEMS = [
    "PERSISTENT.NS", "COFORGE.NS", "TATAELXSI.NS", "LTIM.NS", "POLYCAB.NS",
    "ABCAPITAL.NS", "IRFC.NS", "IEX.NS", "DEEPAKNTR.NS", "MUTHOOTFIN.NS",
    "TATACOMM.NS", "CUMMINSIND.NS", "FEDERALBNK.NS", "IDFCFIRSTB.NS",
    "ESCORTS.NS", "JUBLFOOD.NS", "MPHASIS.NS", "PAGEIND.NS", "AUROPHARMA.NS",
    "VOLTAS.NS", "MAXHEALTH.NS", "OBEROIRLTY.NS", "PRESTIGE.NS", "DIXON.NS",
    "KALYANKJIL.NS", "PHOENIXLTD.NS", "KPITTECH.NS", "SONACOMS.NS",
]

# ── AFFORDABLE STOCKS (under ₹500) — best for ₹3,000 capital ──────────────────
AFFORDABLE = [
    "NHPC.NS", "IRFC.NS", "PNB.NS", "IOC.NS", "COALINDIA.NS",
    "NMDC.NS", "BEL.NS", "CANBK.NS", "BANKBARODA.NS", "HINDPETRO.NS",
    "BPCL.NS", "ITC.NS", "TATAPOWER.NS", "VEDL.NS", "GAIL.NS",
]

# ── Combined unique watchlist ──────────────────────────────────────────────────
# Remove duplicates while preserving order
_all = NIFTY_50 + NIFTY_NEXT_50 + MIDCAP_GEMS + AFFORDABLE
_seen = set()
WATCHLIST = []
for t in _all:
    if t not in _seen:
        _seen.add(t)
        WATCHLIST.append(t)

# Sectors mapped to tickers (for sector rotation analysis)
SECTORS = {
    "IT":        ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS", "PERSISTENT.NS", "COFORGE.NS", "LTIM.NS"],
    "Banking":   ["HDFCBANK.NS", "ICICIBANK.NS", "AXISBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "PNB.NS", "BANKBARODA.NS"],
    "Pharma":    ["SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "AUROPHARMA.NS"],
    "Auto":      ["MARUTI.NS", "TATAMOTORS.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS", "HEROMOTOCO.NS", "TVSMOTOR.NS"],
    "Energy":    ["RELIANCE.NS", "ONGC.NS", "NTPC.NS", "COALINDIA.NS", "POWERGRID.NS", "TATAPOWER.NS", "NHPC.NS"],
    "Metals":    ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "NMDC.NS"],
    "FMCG":      ["HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS", "MARICO.NS", "COLPAL.NS"],
    "Telecom":   ["BHARTIARTL.NS"],
    "Infra":     ["LT.NS", "POLYCAB.NS", "DLF.NS", "ULTRACEMCO.NS", "AMBUJACEM.NS"],
    "Defence":   ["HAL.NS", "BEL.NS"],
    "Retail":    ["TRENT.NS", "DMART.NS", "ZOMATO.NS"],
}
