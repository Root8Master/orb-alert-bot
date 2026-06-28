"""
ORB Scanner — 7 conditions:
1. Close > Opening Range High (30-min)
2. Close > VWAP
3. RSI(14) > 60
4. Volume > MA(Volume, 20)
5. High(0) > High(-1)
6. ADX(14) > 20
7. Gap-up filter: today's open NOT more than GAP_PCT above prev close
   (artificially gapped stocks skipped — ORH too easy to breach)
Only fires 9:20–11:00 IST.
"""
import os
import pandas as pd
from datetime import datetime, time
import pytz
from src.exchange.upstox_client import get_candles, get_orh, get_prev_close

IST        = pytz.timezone("Asia/Kolkata")
SCAN_START = time(9, 20)
SCAN_END   = time(11, 0)
GAP_PCT    = float(os.getenv("GAP_UP_FILTER_PCT", "0.02"))  # 2% default

# Cache prev_close per symbol per day — expensive call, only needed once
_prev_close_cache: dict = {}


def _today_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _get_prev_close_cached(symbol: str, instrument_key: str) -> float | None:
    key = f"{symbol}:{_today_str()}"
    if key not in _prev_close_cache:
        _prev_close_cache[key] = get_prev_close(instrument_key)
    return _prev_close_cache[key]


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return (100 - 100 / (1 + rs)).iloc[-1]


def _vwap(df: pd.DataFrame) -> float:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).sum() / df["volume"].sum()


def _adx(df: pd.DataFrame, period: int = 14) -> float:
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)

    dm_plus  = high.diff().clip(lower=0)
    dm_minus = (-low.diff()).clip(lower=0)
    dm_plus  = dm_plus.where(dm_plus > dm_minus, 0)
    dm_minus = dm_minus.where(dm_minus > dm_plus, 0)

    atr      = tr.ewm(span=period, adjust=False).mean()
    di_plus  = 100 * dm_plus.ewm(span=period, adjust=False).mean() / atr
    di_minus = 100 * dm_minus.ewm(span=period, adjust=False).mean() / atr
    dx       = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus)).fillna(0)
    return round(dx.ewm(span=period, adjust=False).mean().iloc[-1], 2)


def scan(symbol: str, instrument_key: str) -> dict | None:
    now = datetime.now(IST).time()
    if not (SCAN_START <= now <= SCAN_END):
        return None

    try:
        orh        = get_orh(instrument_key)
        candles_5m = get_candles(instrument_key, "5minute")
    except Exception as e:
        print(f"[{symbol}] fetch error: {e}")
        return None

    if not orh or len(candles_5m) < 20:
        return None

    df = pd.DataFrame(candles_5m, columns=["ts","open","high","low","close","volume","oi"])

    today_open = df["open"].iloc[0]
    close      = df["close"].iloc[-1]
    high_0     = df["high"].iloc[-1]
    high_1     = df["high"].iloc[-2]
    vwap       = _vwap(df)
    rsi        = _rsi(df["close"])
    adx_val    = _adx(df)
    vol        = df["volume"].iloc[-1]
    vol_ma     = df["volume"].rolling(20).mean().iloc[-1]

    # ── Gap-up filter ────────────────────────────────────────
    prev_close  = _get_prev_close_cached(symbol, instrument_key)
    gap_pct     = 0.0
    gap_too_big = False
    if prev_close and prev_close > 0:
        gap_pct     = (today_open - prev_close) / prev_close
        gap_too_big = gap_pct > GAP_PCT
    # ─────────────────────────────────────────────────────────

    conditions = {
        "close_gt_orh":   close > orh,
        "close_gt_vwap":  close > vwap,
        "rsi_gt_60":      rsi > 60,
        "vol_gt_ma":      vol > vol_ma,
        "higher_high":    high_0 > high_1,
        "adx_gt_20":      adx_val > 20,
        "no_gap_up":      not gap_too_big,
    }

    if not all(conditions.values()):
        if gap_too_big:
            print(f"[{symbol}] skipped — gap-up {gap_pct*100:.1f}% > {GAP_PCT*100:.0f}%")
        return None

    return {
        "symbol":     symbol,
        "close":      round(close, 2),
        "orh":        round(orh, 2),
        "vwap":       round(vwap, 2),
        "rsi":        round(rsi, 2),
        "adx":        adx_val,
        "volume":     int(vol),
        "vol_ma":     int(vol_ma),
        "high_0":     round(high_0, 2),
        "high_1":     round(high_1, 2),
        "gap_pct":    round(gap_pct * 100, 2),
        "prev_close": round(prev_close, 2) if prev_close else None,
        "conditions": conditions,
    }
