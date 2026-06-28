"""
Pre-market Watchlist.
Runs at 09:00 AM IST.
Fetches previous day's last candle volume + today's first available candle.
Ranks by volume ratio and sends Telegram watchlist before market opens.
"""
import json
from pathlib import Path
from datetime import datetime
import pytz

from src.exchange.upstox_client import get_candles

IST          = pytz.timezone("Asia/Kolkata")
_sym_path    = Path(__file__).parent.parent / "data" / "symbols.json"
ALL_SYMBOLS  = json.loads(_sym_path.read_text())
TOP_N        = int(10)  # top 10 stocks to watch


def _flatten_symbols() -> dict:
    """Merge NIFTY50 + BANKNIFTY into one dict, dedupe."""
    merged = {}
    for stocks in ALL_SYMBOLS.values():
        merged.update(stocks)
    return merged


def build_watchlist() -> list[dict]:
    """
    Returns top N stocks ranked by: today early volume vs prev-day avg.
    Uses 1-min candles to get earliest data after 9:15.
    """
    symbols = _flatten_symbols()
    scored  = []

    for symbol, key in symbols.items():
        try:
            candles = get_candles(key, "1minute")
            if len(candles) < 5:
                continue

            # sum first 5 min volume as proxy for early interest
            early_vol = sum(c[5] for c in candles[:5])
            # prev day avg via last 75 candles (1 full day = ~375 1-min candles, use tail)
            prev_vol_avg = sum(c[5] for c in candles[-75:]) / 75 if len(candles) >= 75 else 0

            ratio = round(early_vol / prev_vol_avg, 2) if prev_vol_avg > 0 else 0
            open_price = candles[0][1]   # first candle open
            ltp        = candles[-1][4]  # last close

            scored.append({
                "symbol":       symbol,
                "open":         round(open_price, 2),
                "ltp":          round(ltp, 2),
                "early_vol":    int(early_vol),
                "vol_ratio":    ratio,
            })
        except Exception as e:
            print(f"[premarket] {symbol} error: {e}")
            continue

    scored.sort(key=lambda x: x["vol_ratio"], reverse=True)
    return scored[:TOP_N]
