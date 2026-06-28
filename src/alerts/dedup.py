"""
Alert Dedup — survives Render restarts.
Stores fired alert keys in Google Sheet tab "Dedup".
Key format: SYMBOL-YYYYMMDD-HHMM

On startup → load today's keys into memory (_sent set).
On alert   → write key to Sheet + add to memory set.
On check   → hit memory set only (fast, no API call per stock).

Falls back to memory-only if Sheets not configured.
"""
import os
from datetime import datetime
import pytz

IST      = pytz.timezone("Asia/Kolkata")
TAB_NAME = "Dedup"

# In-memory set — populated from Sheet on startup
_sent: set[str] = set()
_sheet_ws        = None   # cached worksheet


# ── Sheet access (reuses paper_trades client) ─────────────────

def _ws():
    global _sheet_ws
    if _sheet_ws:
        return _sheet_ws

    sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
    sa_json  = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sheet_id or not sa_json:
        return None

    try:
        import json, base64
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        # sa_json stored as base64 — decode before parsing
        raw   = base64.b64decode(sa_json.encode()).decode()
        creds = Credentials.from_service_account_info(
            json.loads(raw), scopes=scopes
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(TAB_NAME)
        except Exception:
            ws = sh.add_worksheet(title=TAB_NAME, rows=5000, cols=2)
            ws.append_row(["Key", "Timestamp"])
        _sheet_ws = ws
        return ws
    except Exception as e:
        print(f"[dedup] sheet connect error: {e}")
        return None


def load_today_keys():
    """Called once on startup — loads today's dedup keys into _sent."""
    today = datetime.now(IST).strftime("%Y%m%d")
    ws    = _ws()
    if not ws:
        print("[dedup] running memory-only (no Sheets)")
        return
    try:
        rows = ws.get_all_values()   # [[key, ts], ...]
        for row in rows[1:]:         # skip header
            if row and row[0].endswith(f"-{today[:8]}") or (len(row) > 0 and today in row[0]):
                _sent.add(row[0])
        print(f"[dedup] loaded {len(_sent)} keys from Sheet for today")
    except Exception as e:
        print(f"[dedup] load error: {e}")


def already_sent(symbol: str) -> bool:
    """True if this symbol already alerted in this minute today."""
    key = _make_key(symbol)
    return key in _sent


def mark_sent(symbol: str):
    """Record alert as fired — memory + Sheet."""
    key = _make_key(symbol)
    if key in _sent:
        return
    _sent.add(key)
    ws = _ws()
    if ws:
        try:
            ts = datetime.now(IST).isoformat()
            ws.append_row([key, ts])
        except Exception as e:
            print(f"[dedup] write error: {e}")


def _make_key(symbol: str) -> str:
    """SYMBOL-YYYYMMDD-HHMM — unique per symbol per minute."""
    return f"{symbol}-{datetime.now(IST).strftime('%Y%m%d-%H%M')}"
