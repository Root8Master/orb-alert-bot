"""
Upstox headless OAuth via TOTP.
Uses `upstox-totp` library — fully automated, no browser, no manual step.

Flow every day at 08:50 IST:
  upstox-totp logs in with TOTP → gets fresh access_token → stores in _TOKEN
  upstox_client reads _TOKEN on every API call.

No refresh_token exists in Upstox — must re-login daily.
"""
import os
import threading
import time
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
_TOKEN: dict = {"access_token": os.environ.get("UPSTOX_ACCESS_TOKEN", ""), "refreshed_at": None}
_lock = threading.Lock()


def get_token() -> str:
    return _TOKEN["access_token"]


def _refresh():
    """Run upstox-totp headless login, update _TOKEN."""
    try:
        from upstox_totp import UpstoxTOTP  # lazy import; only runs in worker thread
        upx = UpstoxTOTP(
            username=os.environ["UPSTOX_MOBILE"],
            password=os.environ["UPSTOX_PASSWORD"],
            pin_code=os.environ["UPSTOX_PIN"],
            totp_secret=os.environ["UPSTOX_TOTP_SECRET"],
            client_id=os.environ["UPSTOX_CLIENT_ID"],
            client_secret=os.environ["UPSTOX_CLIENT_SECRET"],
            redirect_uri=os.environ["UPSTOX_REDIRECT_URI"],
        )
        resp = upx.app_token.get_access_token()
        if resp.success and resp.data:
            with _lock:
                _TOKEN["access_token"] = resp.data.access_token
                _TOKEN["refreshed_at"] = datetime.now(IST).isoformat()
            print(f"[auth] Token refreshed at {_TOKEN['refreshed_at']}")
        else:
            print(f"[auth] Token refresh FAILED: {resp.error}")
    except Exception as e:
        print(f"[auth] Token refresh ERROR: {e}")


def _next_refresh_seconds() -> float:
    """Seconds until 08:50 IST tomorrow (or today if not yet reached)."""
    now = datetime.now(IST)
    target = now.replace(hour=8, minute=50, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def force_refresh() -> dict:
    """Manually trigger a refresh right now. Returns current token state after attempt."""
    _refresh()
    return {
        "token_set":          bool(_TOKEN["access_token"]),
        "refreshed_at":       _TOKEN["refreshed_at"],
        "token_preview":      (_TOKEN["access_token"][:12] + "...") if _TOKEN["access_token"] else None,
    }


def start_refresh_loop():
    """
    Runs in daemon thread.
    Refreshes immediately on startup (if no env token), then daily at 08:50 IST.
    """
    def loop():
        # Immediate refresh if no token provided via env
        if not _TOKEN["access_token"]:
            print("[auth] No env token found. Fetching now...")
            _refresh()
        else:
            print(f"[auth] Using env token. Next auto-refresh in {_next_refresh_seconds()/3600:.1f}h")

        while True:
            sleep_secs = _next_refresh_seconds()
            print(f"[auth] Sleeping {sleep_secs/3600:.1f}h until next token refresh")
            time.sleep(sleep_secs)
            _refresh()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
