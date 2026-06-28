"""
Sector filter.
Before scanning a stock, check if its sector index is green (positive on the day).
If sector index is red → skip all stocks in that sector.
Cache index status for 5 min to avoid hammering API.
"""
import json
import time
from pathlib import Path
from src.exchange.upstox_client import get_candles

_sectors_path = Path(__file__).parent.parent / "data" / "sectors.json"
_data         = json.loads(_sectors_path.read_text())

SECTOR_MAP       = _data["sector_index_map"]      # sector → index name
INDEX_INSTRUMENTS = _data["index_instruments"]     # index name → instrument key
SYMBOL_SECTOR    = {}                              # symbol → sector (built once below)

for sector, symbols in _data["sectors"].items():
    for sym in symbols:
        SYMBOL_SECTOR[sym] = sector

# Cache: {index_name: (is_green: bool, ts: float)}
_cache: dict = {}
CACHE_TTL = 300  # 5 min


def _index_is_green(index_name: str) -> bool:
    """True if index close > open on latest candle."""
    now = time.time()
    if index_name in _cache and now - _cache[index_name][1] < CACHE_TTL:
        return _cache[index_name][0]

    instrument_key = INDEX_INSTRUMENTS.get(index_name)
    if not instrument_key:
        return True  # unknown index → don't block

    try:
        candles = get_candles(instrument_key, "5minute")
        if not candles:
            return True
        last = candles[-1]
        # candle: [ts, open, high, low, close, volume, oi]
        is_green = last[4] > last[1]  # close > open
        _cache[index_name] = (is_green, now)
        return is_green
    except Exception as e:
        print(f"[sector] index fetch error {index_name}: {e}")
        return True  # fail open — don't block on API error


def sector_is_green(symbol: str) -> bool:
    """
    Returns True if symbol's sector index is positive on the day.
    Returns True for sectors with no index mapping (FINANCE, INFRA, OTHERS).
    """
    sector     = SYMBOL_SECTOR.get(symbol)
    if not sector:
        return True
    index_name = SECTOR_MAP.get(sector)
    if not index_name:
        return True  # no index for this sector → allow
    return _index_is_green(index_name)
