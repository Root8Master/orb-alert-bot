"""
FastAPI app — orchestrates all modules.

Scheduled jobs (IST):
  08:50 → token refresh
  09:00 → pre-market watchlist  (skipped on holidays)
  09:20 → ORB scan loop starts  (skipped on holidays)
  09:30 → watchdog check        (warns if scan hasn't run)
  11:00 → scan loop stops
  15:25 → EOD close open trades
  16:00 → daily summary

Telegram commands: /status /scan /trades /pause /resume /sl SYMBOL PRICE /help

Upgrades active:
  #1  — market holiday skip (holidays.py)
  #2  — dedup across restarts (dedup.py → Google Sheet Dedup tab)
  #4  — silent failure watchdog (watchdog.py → Telegram ⚠️ alert by 09:30)
"""
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytz
from fastapi import FastAPI

from src.auth import start_refresh_loop, force_refresh
from src.scanner.orb_scanner import scan
from src.scanner.sector_filter import sector_is_green
from src.scanner.premarket import build_watchlist
from src.scanner.holidays import is_market_holiday, get_holiday_name
from src.scanner.watchdog import check as watchdog_check, record_scan, reset_daily
from src.tracker.paper_trades import (
    init_db, log_entry, check_exits, eod_close_all,
    get_today_summary, update_sl,
)
from src.alerts.telegram import (
    send_alert, send_exit_alert, send_watchlist,
    send_daily_summary, send_heartbeat,
    send_holiday_notice, send_warning,
)
from src.alerts.dedup import load_today_keys
import src.alerts.telegram_commands as _cmd_module
from src.alerts.telegram_commands import start_command_listener, _scan_trigger

app = FastAPI(title="ORB Alert Bot")
IST = pytz.timezone("Asia/Kolkata")

INTERVAL  = int(os.getenv("INTERVAL_SECONDS", 300))
HEARTBEAT = int(os.getenv("HEARTBEAT_MINUTES", 60))

_sym_path   = Path(__file__).parent / "data" / "symbols.json"
ALL_SYMBOLS = json.loads(_sym_path.read_text())

_state = {
    "last_run":       None,
    "signals":        [],
    "errors":         [],
    "is_holiday":     False,
    "holiday_reason": None,
    "started_at":     None,
}


# ── helpers ──────────────────────────────────────────────────

def _hhmm() -> str:
    return datetime.now(IST).strftime("%H:%M")


def _is_paused() -> bool:
    return _cmd_module.scanner_paused


def _current_prices() -> dict:
    from src.exchange.upstox_client import get_candles
    prices = {}
    for stocks in ALL_SYMBOLS.values():
        for symbol, key in stocks.items():
            try:
                candles = get_candles(key, "5minute")
                if candles:
                    prices[symbol] = candles[-1][4]
            except Exception:
                pass
    return prices


# ── scheduled tasks ──────────────────────────────────────────

def _task_premarket():
    try:
        watchlist = build_watchlist()
        send_watchlist(watchlist)
        print(f"[premarket] sent — {len(watchlist)} stocks")
    except Exception as e:
        print(f"[premarket] error: {e}")


def _task_scan():
    if _is_paused():
        print("[scanner] paused — skip")
        return []

    signals = []
    for index_name, stocks in ALL_SYMBOLS.items():
        for symbol, key in stocks.items():
            if not sector_is_green(symbol):
                continue
            result = scan(symbol, key)
            if result:
                sent = send_alert(result, index_name)   # dedup inside
                if sent:
                    trade_id = log_entry(result, index_name)
                    result["trade_id"] = trade_id
                    signals.append({**result, "index": index_name})

    # exit check
    prices = _current_prices()
    closed = check_exits(prices)
    for t in closed:
        send_exit_alert(t)
        print(f"[exit] {t.get('Symbol', t.get('symbol','?'))} "
              f"{t.get('exit_reason','?')} {t.get('pnl_pct',0):+.2f}%")

    record_scan()   # tell watchdog a scan ran
    _state["last_run"] = datetime.now(IST).isoformat()
    _state["signals"]  = signals
    return signals


def _task_eod_close():
    try:
        prices = _current_prices()
        closed = eod_close_all(prices)
        for t in closed:
            send_exit_alert(t)
        print(f"[eod] closed {len(closed)} trades")
    except Exception as e:
        print(f"[eod] error: {e}")


def _task_daily_summary():
    try:
        summary = get_today_summary()
        send_daily_summary(summary)
        print(f"[summary] sent — {summary['total_trades']} trades")
    except Exception as e:
        print(f"[summary] error: {e}")


# ── main loop ─────────────────────────────────────────────────

def _main_loop():
    _premarket_done  = False
    _holiday_noticed = False
    _eod_done        = False
    _summary_done    = False
    _hb_secs         = 0
    _last_day        = None

    while True:
        now = _hhmm()
        today = datetime.now(IST).date()

        # ── midnight reset ───────────────────────────────────
        if _last_day and _last_day != today:
            _premarket_done  = False
            _holiday_noticed = False
            _eod_done        = False
            _summary_done    = False
            _hb_secs         = 0
            reset_daily()
            load_today_keys()   # reload dedup keys for new day
            _state["is_holiday"]     = False
            _state["holiday_reason"] = None
        _last_day = today

        # ── holiday check (once per day, early) ──────────────
        if not _holiday_noticed and "08:00" <= now:
            if is_market_holiday():
                reason = get_holiday_name()
                _holiday_noticed          = True
                _state["is_holiday"]      = True
                _state["holiday_reason"]  = reason
                send_holiday_notice(reason)
                print(f"[holiday] {reason} — bot sleeping")

        # Skip all trading tasks on holidays
        if _state["is_holiday"]:
            time.sleep(INTERVAL)
            continue

        # ── 09:00 — pre-market watchlist ─────────────────────
        if "09:00" <= now < "09:15" and not _premarket_done:
            _premarket_done = True
            threading.Thread(target=_task_premarket, daemon=True).start()

        # ── 09:20–11:00 — ORB scan loop ──────────────────────
        triggered = _scan_trigger.is_set()
        if triggered:
            _scan_trigger.clear()

        if triggered or ("09:20" <= now <= "11:00"):
            try:
                _task_scan()
            except Exception as e:
                _state["errors"].append(str(e))
                print(f"[scanner] error: {e}")

            _hb_secs += INTERVAL
            if HEARTBEAT > 0 and _hb_secs >= HEARTBEAT * 60:
                send_heartbeat()
                _hb_secs = 0

        # ── watchdog: warn if scan missed by 09:30 ───────────
        watchdog_check(send_warning_fn=send_warning)

        # ── 15:25 — EOD close ────────────────────────────────
        if "15:25" <= now < "15:35" and not _eod_done:
            _eod_done = True
            threading.Thread(target=_task_eod_close, daemon=True).start()

        # ── 16:00 — daily summary ────────────────────────────
        if "16:00" <= now < "16:10" and not _summary_done:
            _summary_done = True
            threading.Thread(target=_task_daily_summary, daemon=True).start()

        time.sleep(INTERVAL)


# ── startup ───────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    load_today_keys()       # #2: load persisted dedup keys
    start_refresh_loop()    # auth

    start_command_listener(
        get_state_fn  = lambda: _state,
        run_scan_fn   = _task_scan,
        update_sl_fn  = update_sl,
    )

    _state["started_at"] = datetime.now(IST).isoformat()
    threading.Thread(target=_main_loop, daemon=True).start()


# ── endpoints ─────────────────────────────────────────────────

@app.api_route("/", methods=["GET", "HEAD"])
def status():
    return {
        "status":         "running",
        "scanner_paused": _is_paused(),
        "is_holiday":     _state["is_holiday"],
        "holiday_reason": _state["holiday_reason"],
        "time_ist":       _hhmm(),
        "last_scan":      _state["last_run"],
        "signals_today":  len(_state["signals"]),
        "signals":        _state["signals"],
        "recent_errors":  _state["errors"][-5:],
    }


@app.api_route("/health", methods=["GET", "HEAD", "POST"])
def health():
    return {"ok": True}


@app.get("/trades")
def trades():
    return get_today_summary()


@app.get("/holiday")
def holiday():
    today = datetime.now(IST).date()
    return {
        "date":       str(today),
        "is_holiday": is_market_holiday(today),
        "reason":     get_holiday_name(today) if is_market_holiday(today) else None,
    }


@app.post("/run-now")
def run_now():
    signals = _task_scan()
    return {"triggered": True, "signals": signals}


@app.post("/run-premarket")
def run_premarket():
    _task_premarket()
    return {"triggered": True}


@app.post("/run-summary")
def run_summary():
    _task_daily_summary()
    return {"triggered": True}


@app.post("/pause")
def pause():
    _cmd_module.scanner_paused = True
    return {"paused": True}


@app.post("/resume")
def resume():
    _cmd_module.scanner_paused = False
    return {"paused": False}


@app.post("/auth-refresh")
def auth_refresh():
    """Manually trigger Upstox TOTP login right now — test without waiting for 08:50."""
    result = force_refresh()
    return result


@app.get("/debug")
def debug():
    """Full runtime state — use this when alerts aren't firing."""
    from src.auth import _TOKEN
    from src.alerts.dedup import _sent as dedup_keys
    now = _hhmm()
    in_window = "09:20" <= now <= "11:00"
    return {
        "time_ist":          now,
        "in_scan_window":    in_window,
        "scanner_paused":    _is_paused(),
        "is_holiday":        _state["is_holiday"],
        "holiday_reason":    _state["holiday_reason"],
        "last_scan":         _state["last_run"],
        "signals_today":     len(_state["signals"]),
        "recent_errors":     _state["errors"][-10:],
        "token_set":         bool(_TOKEN.get("access_token")),
        "token_refreshed_at": _TOKEN.get("refreshed_at"),
        "dedup_keys_today":  len(dedup_keys),
        "uptime_since":      _state.get("started_at"),
    }
