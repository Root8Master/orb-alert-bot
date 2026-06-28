"""
Upstox v2 intraday + historical candle fetcher.
- get_candles()      : today's intraday candles
- get_orh()          : opening range high (first 30-min candle)
- get_prev_close()   : previous day's closing price (for gap-up filter)
- Rate limit: exponential backoff on 429, up to MAX_RETRIES attempts
"""
import time
import requests
from src.auth import get_token

BASE_INTRADAY   = "https://api.upstox.com/v2/historical-candle/intraday"
BASE_HISTORICAL = "https://api.upstox.com/v2/historical-candle"

MAX_RETRIES = 4
BASE_DELAY  = 1.0   # seconds, doubles each retry


def _headers() -> dict:
    return {
        "Accept":        "application/json",
        "Authorization": f"Bearer {get_token()}"
    }


def _get(url: str) -> dict:
    """
    GET with exponential backoff on 429 / 5xx.
    Raises on unrecoverable errors.
    """
    delay = BASE_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=_headers(), timeout=10)

            if r.status_code == 429:
                wait = delay * (2 ** (attempt - 1))
                print(f"[api] rate limit hit — waiting {wait:.1f}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue

            if r.status_code >= 500:
                wait = delay * (2 ** (attempt - 1))
                print(f"[api] server error {r.status_code} — retrying in {wait:.1f}s")
                time.sleep(wait)
                continue

            r.raise_for_status()
            return r.json()

        except requests.exceptions.Timeout:
            wait = delay * (2 ** (attempt - 1))
            print(f"[api] timeout — retrying in {wait:.1f}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(delay)
            continue

    raise RuntimeError(f"[api] max retries exceeded for {url}")


def get_candles(instrument_key: str, interval: str = "5minute") -> list:
    """
    Intraday candles sorted oldest→newest.
    interval: 1minute | 5minute | 30minute
    Candle format: [timestamp, open, high, low, close, volume, oi]
    """
    url     = f"{BASE_INTRADAY}/{instrument_key}/{interval}"
    data    = _get(url)
    candles = data.get("data", {}).get("candles", [])
    return list(reversed(candles))


def get_orh(instrument_key: str) -> float | None:
    """Opening Range High: high of first 30-min candle (9:15–9:45)."""
    candles = get_candles(instrument_key, "30minute")
    if not candles:
        return None
    return candles[0][2]  # index 2 = high


def get_prev_close(instrument_key: str) -> float | None:
    """
    Previous trading day's closing price.
    Uses historical endpoint — date param: 'YYYY-MM-DD'
    Returns None on error (gap filter skips gracefully).
    """
    from datetime import datetime, timedelta
    import pytz
    IST  = pytz.timezone("Asia/Kolkata")
    # go back up to 5 days to find last trading day
    today = datetime.now(IST).date()
    for days_back in range(1, 6):
        d   = today - timedelta(days=days_back)
        url = (f"{BASE_HISTORICAL}/{instrument_key}/day"
               f"/{d.strftime('%Y-%m-%d')}/{d.strftime('%Y-%m-%d')}")
        try:
            data    = _get(url)
            candles = data.get("data", {}).get("candles", [])
            if candles:
                return candles[0][4]   # close price
        except Exception:
            continue
    return None
