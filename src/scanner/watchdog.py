"""
Silent Failure Watchdog.
Checks that scanner actually ran during expected window (09:20–11:00 IST).
If no scan logged by 09:30 on a trading day → fires Telegram warning.
Runs as a lightweight check inside main loop — no extra thread needed.
"""
from datetime import datetime, time
import pytz

IST           = pytz.timezone("Asia/Kolkata")
WARN_BY       = time(9, 30)     # if no scan by this time, warn
WINDOW_START  = time(9, 20)
WINDOW_END    = time(11, 0)

_warned_today: bool = False
_scan_ran_today: bool = False


def reset_daily():
    """Call at midnight."""
    global _warned_today, _scan_ran_today
    _warned_today   = False
    _scan_ran_today = False


def record_scan():
    """Call every time a scan cycle completes successfully."""
    global _scan_ran_today
    _scan_ran_today = True


def check(send_warning_fn) -> bool:
    """
    Call from main loop every cycle.
    Returns True if warning was just fired (so caller can log it).
    send_warning_fn: callable that sends a Telegram message string.
    """
    global _warned_today

    now = datetime.now(IST).time()

    # Only check once per day, only inside warning window
    if _warned_today:
        return False
    if not (WINDOW_START <= now <= WARN_BY):
        return False
    if _scan_ran_today:
        return False

    # Scanner should have run by now but hasn't
    _warned_today = True
    msg = (
        f"⚠️ *WATCHDOG ALERT*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Scanner has not run since market open.\n"
        f"Expected by `09:30 IST` — nothing logged.\n\n"
        f"Possible causes:\n"
        f"• Upstox token expired\n"
        f"• Render cold start delay\n"
        f"• API rate limit at open\n\n"
        f"⏰ `{datetime.now(IST).strftime('%I:%M %p IST')}`\n"
        f"Use /status to check bot state."
    )
    send_warning_fn(msg)
    return True
