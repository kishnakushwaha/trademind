"""
data/fetcher.py
Fetches OHLCV data using raw Yahoo Finance API with:
  - Local disk cache (avoids re-downloading same-day data)
  - Exponential backoff retry (handles rate limits gracefully)
  - News scraping from MoneyControl and Economic Times RSS
"""

import os
import json
import time
import hashlib
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
import logging

from config.settings import HISTORICAL_PERIOD, DATA_INTERVAL

logger = logging.getLogger(__name__)

# ── Cache config ───────────────────────────────────────────────────────────────
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

YAHOO_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}


def _cache_path(ticker: str, period: str, interval: str) -> str:
    """Generate a cache file path for a ticker + params combo."""
    safe_ticker = ticker.replace('.', '_')
    key = f"{safe_ticker}_{period}_{interval}_{date.today().isoformat()}"
    return os.path.join(CACHE_DIR, f"{key}.csv")


def _load_cache(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    """Load cached data if it exists and is from today."""
    path = _cache_path(ticker, period, interval)
    if os.path.exists(path):
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            logger.debug(f"Cache HIT for {ticker} ({len(df)} rows)")
            return df
        except Exception:
            pass
    return None


def _save_cache(df: pd.DataFrame, ticker: str, period: str, interval: str):
    """Save data to cache."""
    try:
        path = _cache_path(ticker, period, interval)
        df.to_csv(path)
    except Exception as e:
        logger.debug(f"Cache save failed for {ticker}: {e}")


def _cleanup_old_cache():
    """Remove cache files from previous days (runs once per session)."""
    today = date.today().isoformat()
    try:
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".csv") and today not in f:
                os.remove(os.path.join(CACHE_DIR, f))
    except Exception:
        pass

# Run cleanup on import
_cleanup_old_cache()


# ── Price data (with cache + retry) ───────────────────────────────────────────

def fetch_ohlcv(ticker: str, period: str = HISTORICAL_PERIOD,
                interval: str = DATA_INTERVAL) -> pd.DataFrame:
    """
    Fetch OHLCV data for a ticker.
    1. Check local cache first (instant, zero API calls)
    2. If no cache, fetch from Yahoo with retry + backoff
    3. Save to cache for future scans today

    Returns DataFrame with columns: Open, High, Low, Close, Volume
    """
    # Step 1: Check cache
    cached = _load_cache(ticker, period, interval)
    if cached is not None:
        return cached

    # Step 2: Fetch from Yahoo with retry
    range_map = {"1mo": "1mo", "3mo": "3mo", "6mo": "6mo", "1y": "1y", "2y": "2y"}
    api_period = range_map.get(period, "1mo")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={api_period}&interval={interval}"

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=YAHOO_HEADERS, timeout=15)

            if resp.status_code == 429:
                # Rate limited — back off exponentially
                wait = 2 ** (attempt + 1)
                logger.warning(f"Rate limited on {ticker}, waiting {wait}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                logger.error(f"Failed to fetch {ticker}: HTTP {resp.status_code}")
                return pd.DataFrame()

            data = resp.json()
            result = data.get('chart', {}).get('result', [None])[0]
            if not result:
                logger.warning(f"No chart data in response for {ticker}")
                return pd.DataFrame()

            timestamps = result.get('timestamp', [])
            indicators = result.get('indicators', {}).get('quote', [{}])[0]
            adj_close  = result.get('indicators', {}).get('adjclose', [{}])
            adj_close  = adj_close[0].get('adjclose', []) if adj_close else []

            df = pd.DataFrame({
                "Open":   indicators.get('open', []),
                "High":   indicators.get('high', []),
                "Low":    indicators.get('low', []),
                "Close":  adj_close if adj_close else indicators.get('close', []),
                "Volume": indicators.get('volume', [])
            }, index=pd.to_datetime(timestamps, unit='s'))

            df.dropna(inplace=True)

            # Step 3: Save to cache
            _save_cache(df, ticker, period, interval)
            logger.info(f"Fetched {len(df)} rows for {ticker}")
            return df

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout for {ticker}, retrying ({attempt+1}/{max_retries})")
            time.sleep(1)
        except Exception as e:
            logger.error(f"Fetch error for {ticker}: {e}")
            return pd.DataFrame()

    logger.error(f"All retries failed for {ticker}")
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
    """Fetch latest price for a ticker using raw requests."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1m"
        resp = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return float(data['chart']['result'][0]['meta']['regularMarketPrice'])
        return None
    except Exception as e:
        logger.error(f"Live price fetch failed for {ticker}: {e}")
        return None


def fetch_stock_info(ticker: str) -> dict:
    """Fetch fundamental info for a ticker from Yahoo raw API."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d"
        resp = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            meta = data['chart']['result'][0]['meta']
            return {
                "pe_ratio":       None,  # Not available in chart API
                "market_cap":     None,
                "52w_high":       meta.get("fiftyTwoWeekHigh", None),
                "52w_low":        meta.get("fiftyTwoWeekLow", None),
                "sector":         "Unknown",
            }
        return {}
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
