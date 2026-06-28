"""
Paper Trade Tracker — Google Sheets backend.
No DB. No disk. No infra. Just a Google Sheet.

Sheet layout (auto-created on first run):
  Col A: ID | B: Date | C: Time | D: Symbol | E: Index
  F: Entry | G: SL | H: Target | I: Exit Price | J: Exit Reason
  K: Exit Time | L: P&L % | M: Status

Auth: Service Account JSON key via GOOGLE_SERVICE_ACCOUNT_JSON env var.
      GOOGLE_SHEET_ID = the sheet's ID from its URL.

Falls back to in-memory if creds not set (safe for local testing).
"""
import os
import json
import time
from datetime import datetime
import pytz

IST     = pytz.timezone("Asia/Kolkata")
SL_PCT  = float(os.getenv("PAPER_SL_PCT",  "0.008"))
TGT_PCT = float(os.getenv("PAPER_TGT_PCT", "0.015"))

SHEET_ID      = os.getenv("GOOGLE_SHEET_ID", "")
SA_JSON       = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SHEET_NAME    = "Trades"
HEADER = ["ID","Date","Time","Symbol","Index","Entry","SL","Target",
          "Exit Price","Exit Reason","Exit Time","P&L %","Status"]

# ── in-memory fallback ────────────────────────────────────────
_mem: list[dict] = []
_mem_id = 0


def _use_sheets() -> bool:
    return bool(SHEET_ID and SA_JSON)


# ── Google Sheets client (lazy, cached) ──────────────────────
_gc = None

def _client():
    global _gc
    if _gc:
        return _gc
    import base64, gspread
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    # SA_JSON stored as base64 — decode before parsing
    raw   = base64.b64decode(SA_JSON.encode()).decode()
    info  = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    _gc   = gspread.authorize(creds)
    return _gc


def _sheet():
    gc = _client()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(SHEET_NAME)
    except Exception:
        ws = sh.add_worksheet(title=SHEET_NAME, rows=1000, cols=len(HEADER))
        ws.append_row(HEADER)
    return ws


def _all_rows() -> list[dict]:
    """Returns all data rows as list of dicts."""
    ws   = _sheet()
    rows = ws.get_all_records()   # uses header row as keys
    return rows


def _find_row_index(trade_id: int) -> int:
    """Returns 1-based row index in sheet (row 1 = header)."""
    ws   = _sheet()
    col  = ws.col_values(1)       # column A = IDs
    for i, val in enumerate(col):
        if str(val) == str(trade_id):
            return i + 1          # 1-based
    return -1


# ── public API ───────────────────────────────────────────────

def init_db():
    if not _use_sheets():
        print("[trades] No Google Sheets creds — using in-memory store")
        return
    try:
        ws = _sheet()
        # ensure header exists
        first = ws.row_values(1)
        if first != HEADER:
            ws.insert_row(HEADER, 1)
        print(f"[trades] Google Sheet ready: {SHEET_ID}")
    except Exception as e:
        print(f"[trades] Sheet init error: {e}")


def log_entry(signal: dict, index_name: str) -> int:
    global _mem_id
    entry  = signal["close"]
    sl     = round(entry * (1 - SL_PCT), 2)
    target = round(entry * (1 + TGT_PCT), 2)
    date   = datetime.now(IST).strftime("%Y-%m-%d")
    t_str  = datetime.now(IST).strftime("%H:%M:%S")

    if not _use_sheets():
        _mem_id += 1
        _mem.append({"ID": _mem_id, "Date": date, "Time": t_str,
                     "Symbol": signal["symbol"], "Index": index_name,
                     "Entry": entry, "SL": sl, "Target": target,
                     "Exit Price": "", "Exit Reason": "", "Exit Time": "",
                     "P&L %": "", "Status": "OPEN"})
        return _mem_id

    try:
        ws = _sheet()
        # next ID = current row count (excluding header)
        trade_id = len(ws.col_values(1))   # header + data rows
        row = [trade_id, date, t_str, signal["symbol"], index_name,
               entry, sl, target, "", "", "", "", "OPEN"]
        ws.append_row(row, value_input_option="USER_ENTERED")
        return trade_id
    except Exception as e:
        print(f"[trades] log_entry error: {e}")
        return -1


def _close_trade_sheet(trade_id: int, price: float, reason: str, pnl_pct: float):
    t_str = datetime.now(IST).strftime("%H:%M:%S")
    try:
        ws  = _sheet()
        idx = _find_row_index(trade_id)
        if idx < 0:
            return
        # Cols I=9, J=10, K=11, L=12, M=13
        ws.update(f"I{idx}:M{idx}", [[price, reason, t_str, pnl_pct, "CLOSED"]])
    except Exception as e:
        print(f"[trades] close_trade error: {e}")


def _open_trades_sheet() -> list[dict]:
    try:
        rows = _all_rows()
        return [r for r in rows if r.get("Status") == "OPEN"]
    except Exception as e:
        print(f"[trades] open_trades error: {e}")
        return []


def check_exits(current_prices: dict) -> list[dict]:
    closed = []
    trades = (_mem if not _use_sheets()
              else _open_trades_sheet())
    open_t = [t for t in trades if t.get("Status") == "OPEN"]

    for t in open_t:
        price = current_prices.get(t["Symbol"])
        if price is None:
            continue
        reason = None
        if price >= float(t["Target"]):
            reason = "TARGET"
        elif price <= float(t["SL"]):
            reason = "SL"
        if reason:
            pnl_pct = round((price - float(t["Entry"])) / float(t["Entry"]) * 100, 2)
            if _use_sheets():
                _close_trade_sheet(int(t["ID"]), price, reason, pnl_pct)
            else:
                t.update({"Exit Price": price, "Exit Reason": reason,
                          "P&L %": pnl_pct, "Status": "CLOSED"})
            closed.append({**t, "exit_price": price,
                           "exit_reason": reason, "pnl_pct": pnl_pct,
                           "entry": float(t["Entry"]),
                           "index_name": t.get("Index","")})
    return closed


def eod_close_all(current_prices: dict) -> list[dict]:
    closed = []
    trades = (_mem if not _use_sheets() else _open_trades_sheet())
    open_t = [t for t in trades if t.get("Status") == "OPEN"]

    for t in open_t:
        price   = current_prices.get(t["Symbol"], float(t["Entry"]))
        pnl_pct = round((price - float(t["Entry"])) / float(t["Entry"]) * 100, 2)
        if _use_sheets():
            _close_trade_sheet(int(t["ID"]), price, "EOD", pnl_pct)
        else:
            t.update({"Exit Price": price, "Exit Reason": "EOD",
                      "P&L %": pnl_pct, "Status": "CLOSED"})
        closed.append({**t, "exit_price": price, "exit_reason": "EOD",
                       "pnl_pct": pnl_pct, "entry": float(t["Entry"]),
                       "index_name": t.get("Index","")})
    return closed


def get_today_summary() -> dict:
    date = datetime.now(IST).strftime("%Y-%m-%d")
    rows = ([r for r in _mem if r["Date"] == date]
            if not _use_sheets()
            else [r for r in _all_rows() if r.get("Date") == date])

    closed  = [r for r in rows if r.get("Status") == "CLOSED"]
    wins    = [r for r in closed if r.get("P&L %") and float(r["P&L %"]) > 0]
    targets = [r for r in closed if r.get("Exit Reason") == "TARGET"]
    sls     = [r for r in closed if r.get("Exit Reason") == "SL"]
    eods    = [r for r in closed if r.get("Exit Reason") == "EOD"]
    avg_pnl = (round(sum(float(r["P&L %"]) for r in closed if r.get("P&L %")) / len(closed), 2)
               if closed else 0)

    return {
        "date": date, "total_trades": len(rows), "closed": len(closed),
        "wins": len(wins), "losses": len(closed) - len(wins),
        "targets_hit": len(targets), "sl_hit": len(sls),
        "eod_closed": len(eods), "avg_pnl_pct": avg_pnl, "trades": rows,
    }


def update_sl(symbol: str, new_sl: float) -> bool:
    """Update SL on the most recent open trade for a symbol. Returns True if found."""
    if not _use_sheets():
        for t in reversed(_mem):
            if t["Symbol"] == symbol and t["Status"] == "OPEN":
                t["SL"] = new_sl
                return True
        return False
    try:
        rows = _all_rows()
        ws   = _sheet()
        for i, r in enumerate(rows):
            if r.get("Symbol") == symbol and r.get("Status") == "OPEN":
                sheet_row = i + 2   # +1 for header, +1 for 1-based index
                # Column G = SL (7th column)
                ws.update_cell(sheet_row, 7, new_sl)
                return True
    except Exception as e:
        print(f"[trades] update_sl error: {e}")
    return False
