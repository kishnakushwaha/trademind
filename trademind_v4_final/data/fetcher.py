"""
data/fetcher.py
Fetches OHLCV data from yfinance for NSE/BSE stocks.
Also scrapes headlines from MoneyControl and Economic Times RSS.
"""

import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import logging

from config.settings import HISTORICAL_PERIOD, DATA_INTERVAL

logger = logging.getLogger(__name__)


# ── Price data ─────────────────────────────────────────────────────────────────

def fetch_ohlcv(ticker: str, period: str = HISTORICAL_PERIOD,
                interval: str = DATA_INTERVAL) -> pd.DataFrame:
    """
    Fetch OHLCV data for a ticker from yfinance.
    Returns DataFrame with columns: Open, High, Low, Close, Volume
    """
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df.empty:
            logger.warning(f"No data returned for {ticker}")
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index)
        df.dropna(inplace=True)
        logger.info(f"Fetched {len(df)} rows for {ticker}")
        return df
    except Exception as e:
        logger.error(f"Error fetching {ticker}: {e}")
        return pd.DataFrame()


def fetch_multiple(tickers: list, period: str = HISTORICAL_PERIOD) -> dict:
    """Fetch OHLCV for a list of tickers. Returns dict of {ticker: DataFrame}"""
    results = {}
    for ticker in tickers:
        df = fetch_ohlcv(ticker, period=period)
        if not df.empty:
            results[ticker] = df
    return results


def fetch_live_price(ticker: str) -> float | None:
    """Fetch latest price for a ticker (for live mode)."""
    try:
        t = yf.Ticker(ticker)
        data = t.history(period="1d", interval="1m")
        if not data.empty:
            return float(data["Close"].iloc[-1])
        return None
    except Exception as e:
        logger.error(f"Live price fetch failed for {ticker}: {e}")
        return None


def fetch_stock_info(ticker: str) -> dict:
    """Fetch fundamental info for a ticker (P/E, market cap, etc.)"""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        return {
            "pe_ratio":          info.get("trailingPE", None),
            "market_cap":        info.get("marketCap", None),
            "debt_to_equity":    info.get("debtToEquity", None),
            "roe":               info.get("returnOnEquity", None),
            "revenue_growth":    info.get("revenueGrowth", None),
            "profit_margin":     info.get("profitMargins", None),
            "52w_high":          info.get("fiftyTwoWeekHigh", None),
            "52w_low":           info.get("fiftyTwoWeekLow", None),
            "sector":            info.get("sector", "Unknown"),
        }
    except Exception as e:
        logger.error(f"Info fetch failed for {ticker}: {e}")
        return {}


# ── News scraping ──────────────────────────────────────────────────────────────

NEWS_SOURCES = {
    "moneycontrol": "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "economic_times": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "nse_announcements": "https://www.nseindia.com/api/corporate-announcements?index=equities",
}


def fetch_news_headlines(max_articles: int = 50) -> list[dict]:
    """
    Scrape financial news headlines from MoneyControl and ET RSS feeds.
    Returns list of dicts with 'title', 'source', 'published'.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TradeMind/1.0)"}
    headlines = []

    for source, url in list(NEWS_SOURCES.items())[:2]:  # skip NSE (needs session)
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(resp.content, "xml")
            items = soup.find_all("item")[:max_articles // 2]

            for item in items:
                title = item.find("title")
                pubdate = item.find("pubDate")
                headlines.append({
                    "title":     title.text.strip() if title else "",
                    "source":    source,
                    "published": pubdate.text.strip() if pubdate else "",
                })
        except Exception as e:
            logger.warning(f"News fetch failed for {source}: {e}")

    logger.info(f"Fetched {len(headlines)} headlines")
    return headlines


def filter_headlines_for_ticker(headlines: list[dict], ticker: str) -> list[dict]:
    """Filter headlines relevant to a specific ticker/company name."""
    # Strip exchange suffix for search
    company = ticker.replace(".NS", "").replace(".BO", "").lower()
    return [h for h in headlines if company in h["title"].lower()]
