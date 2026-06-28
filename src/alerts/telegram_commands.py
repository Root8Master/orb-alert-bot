"""
Telegram Bot command handler.
Listens for incoming messages via long-polling in a background thread.
Commands:
  /status   → last scan time, scanner state, open trades count
  /scan     → trigger immediate scan
  /trades   → today's trade log with P&L
  /pause    → pause scanner
  /resume   → resume scanner
  /sl <SYMBOL> <PRICE> → update stop-loss on open trade

Runs in its own daemon thread. Shares _scanner_paused flag with web.py via module-level state.
"""
import os
import time
import threading
import requests
from datetime import datetime
import pytz

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = str(os.environ["TELEGRAM_CHAT_ID"])
IST       = pytz.timezone("Asia/Kolkata")

BASE      = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── shared state (read by web.py) ────────────────────────────
scanner_paused: bool = False
_last_offset:   int  = 0
_scan_trigger:  threading.Event = threading.Event()   # web.py watches this


def _post(text: str, chat_id: str = None):
    requests.post(f"{BASE}/sendMessage", json={
        "chat_id":    chat_id or CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }, timeout=10)


def _get_updates(offset: int) -> list:
    try:
        r = requests.get(f"{BASE}/getUpdates", params={
            "offset":  offset,
            "timeout": 30,         # long-poll 30s
        }, timeout=35)
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception as e:
        print(f"[bot] getUpdates error: {e}")
    return []


def _handle(msg: dict, get_state_fn, run_scan_fn, update_sl_fn):
    global scanner_paused

    chat_id = str(msg.get("chat", {}).get("id", ""))
    text    = msg.get("text", "").strip()

    # only respond to configured chat
    if chat_id != CHAT_ID:
        return

    cmd = text.split()[0].lower().replace("@", "").split("@")[0]
    args = text.split()[1:]

    if cmd == "/status":
        state = get_state_fn()
        status_emoji = "⏸" if scanner_paused else "🟢"
        _post(
            f"*ORB Bot Status*\n"
            f"━━━━━━━━━━━━━━\n"
            f"{status_emoji} Scanner: `{'PAUSED' if scanner_paused else 'RUNNING'}`\n"
            f"🕐 Last scan: `{state.get('last_run', 'N/A')}`\n"
            f"📊 Signals today: `{len(state.get('signals', []))}`\n"
            f"⏰ Time IST: `{datetime.now(IST).strftime('%I:%M %p')}`"
        )

    elif cmd == "/scan":
        if scanner_paused:
            _post("⏸ Scanner is paused. Use /resume first.")
            return
        _post("🔄 Manual scan triggered...")
        _scan_trigger.set()

    elif cmd == "/trades":
        from src.tracker.paper_trades import get_today_summary
        s = get_today_summary()
        if not s["total_trades"]:
            _post("📋 No trades today yet.")
            return
        win_rate = round(s["wins"] / s["closed"] * 100) if s["closed"] else 0
        lines = ""
        for t in s["trades"]:
            if t.get("Status") == "CLOSED":
                pnl  = t.get("P&L %", 0) or 0
                em   = "✅" if float(pnl) > 0 else "❌"
                lines += f"{em} {t['Symbol']} | {t.get('Exit Reason','?')} | `{float(pnl):+.2f}%`\n"
            else:
                lines += f"🔵 {t['Symbol']} | OPEN | Entry `{t['Entry']}`\n"
        _post(
            f"📋 *Today's Trades — {s['date']}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"{lines}\n"
            f"🏆 Win rate: `{win_rate}%` | Avg P&L: `{s['avg_pnl_pct']:+.2f}%`"
        )

    elif cmd == "/pause":
        scanner_paused = True
        _post("⏸ Scanner *paused*. No alerts will fire until /resume.")

    elif cmd == "/resume":
        scanner_paused = False
        _post("▶️ Scanner *resumed*.")

    elif cmd == "/sl":
        if len(args) < 2:
            _post("Usage: `/sl SYMBOL PRICE`\nExample: `/sl RELIANCE 2900`")
            return
        symbol    = args[0].upper()
        try:
            new_sl = float(args[1])
        except ValueError:
            _post(f"❌ Invalid price: `{args[1]}`")
            return
        result = update_sl_fn(symbol, new_sl)
        if result:
            _post(f"✅ SL updated — *{symbol}* → `{new_sl}`")
        else:
            _post(f"❌ No open trade found for *{symbol}*")

    elif cmd == "/help":
        _post(
            "*ORB Bot Commands*\n"
            "━━━━━━━━━━━━━━\n"
            "/status — scanner state + last scan\n"
            "/scan   — trigger manual scan now\n"
            "/trades — today's trade P&L\n"
            "/pause  — pause scanner\n"
            "/resume — resume scanner\n"
            "/sl SYMBOL PRICE — update stop-loss\n"
            "/help   — this message"
        )


def start_command_listener(get_state_fn, run_scan_fn, update_sl_fn):
    """Start long-poll loop in background daemon thread."""
    def loop():
        global _last_offset
        print("[bot] Command listener started")
        while True:
            updates = _get_updates(_last_offset)
            for u in updates:
                _last_offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message")
                if msg and msg.get("text", "").startswith("/"):
                    try:
                        _handle(msg, get_state_fn, run_scan_fn, update_sl_fn)
                    except Exception as e:
                        print(f"[bot] handler error: {e}")
            # no sleep needed — long-poll already waits 30s

    t = threading.Thread(target=loop, daemon=True)
    t.start()
