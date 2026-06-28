"""
Telegram alerts — all message types.
Dedup now handled by dedup.py (persists across Render restarts).
"""
import os
import requests
from datetime import datetime
import pytz

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
IST       = pytz.timezone("Asia/Kolkata")
TV_BASE   = "https://www.tradingview.com/chart/?symbol=NSE:{symbol}"


def _post(msg: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id":                  CHAT_ID,
            "text":                     msg,
            "parse_mode":               "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=10,
    )


# ── Entry Alert ──────────────────────────────────────────────
def send_alert(signal: dict, index_name: str) -> bool:
    """
    Returns True if alert was sent, False if deduped.
    Dedup check + mark done here — caller just calls send_alert().
    """
    from src.alerts.dedup import already_sent, mark_sent
    if already_sent(signal["symbol"]):
        return False
    mark_sent(signal["symbol"])

    c       = signal["conditions"]
    tv_link = TV_BASE.format(symbol=signal["symbol"])

    _post(
        f"🚨 *ORB BREAKOUT — {index_name}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 *{signal['symbol']}*\n"
        f"💰 Close: `{signal['close']}`\n\n"
        f"{'✅' if c['close_gt_orh']  else '❌'} Close `{signal['close']}` > ORH `{signal['orh']}`\n"
        f"{'✅' if c['close_gt_vwap'] else '❌'} Close > VWAP `{signal['vwap']}`\n"
        f"{'✅' if c['rsi_gt_60']     else '❌'} RSI(14): `{signal['rsi']}`\n"
        f"{'✅' if c['adx_gt_20']     else '❌'} ADX(14): `{signal['adx']}`\n"
        f"{'✅' if c['vol_gt_ma']     else '❌'} Volume `{signal['volume']:,}` > MA `{signal['vol_ma']:,}`\n"
        f"{'✅' if c['higher_high']   else '❌'} High `{signal['high_0']}` > Prev `{signal['high_1']}`\n"
        f"{'✅' if c['no_gap_up']     else '❌'} Gap: `{signal.get('gap_pct', 0):+.1f}%`"
        f" (prev close `{signal.get('prev_close', 'N/A')}`)\n\n"
        f"🎯 Target: `{round(signal['close'] * 1.015, 2)}`"
        f"  |  🛑 SL: `{round(signal['close'] * 0.992, 2)}`\n\n"
        f"📊 [TradingView]({tv_link})\n"
        f"⏰ {datetime.now(IST).strftime('%I:%M %p IST')}"
    )
    return True


# ── Exit Alert ───────────────────────────────────────────────
def send_exit_alert(trade: dict) -> None:
    emoji  = "✅" if trade["exit_reason"] == "TARGET" else (
             "🛑" if trade["exit_reason"] == "SL" else "🔔")
    pnl    = trade["pnl_pct"]
    pnl_em = "🟢" if pnl > 0 else "🔴"

    _post(
        f"{emoji} *EXIT — {trade.get('index_name', '')}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 *{trade['symbol']}*\n"
        f"📥 Entry:  `{trade['entry']}`\n"
        f"📤 Exit:   `{trade['exit_price']}` ({trade['exit_reason']})\n"
        f"{pnl_em} P&L:   `{'+' if pnl > 0 else ''}{pnl}%`\n"
        f"⏰ {datetime.now(IST).strftime('%I:%M %p IST')}"
    )


# ── Pre-market Watchlist ─────────────────────────────────────
def send_watchlist(stocks: list[dict]) -> None:
    if not stocks:
        return
    lines = "\n".join(
        f"{i+1}. *{s['symbol']}* — Vol ratio: `{s['vol_ratio']}x`  LTP: `{s['ltp']}`"
        for i, s in enumerate(stocks)
    )
    _post(
        f"🔭 *PRE-MARKET WATCHLIST*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Top stocks by early volume surge:\n\n"
        f"{lines}\n\n"
        f"⏰ {datetime.now(IST).strftime('%I:%M %p IST')} | ORB scan starts 09:20"
    )


# ── Daily Summary ────────────────────────────────────────────
def send_daily_summary(summary: dict) -> None:
    t        = summary
    win_rate = round(t["wins"] / t["closed"] * 100) if t["closed"] else 0
    pnl_em   = "🟢" if t["avg_pnl_pct"] >= 0 else "🔴"

    trade_lines = ""
    for tr in t["trades"]:
        if tr.get("Status") == "CLOSED":
            pnl = tr.get("P&L %", 0) or 0
            em  = "✅" if float(pnl) > 0 else "❌"
            rsn = tr.get("Exit Reason", "?")
            trade_lines += f"{em} {tr['Symbol']} | {rsn} | `{float(pnl):+.2f}%`\n"

    _post(
        f"📊 *DAILY SUMMARY — {t['date']}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📈 Total Alerts:  `{t['total_trades']}`\n"
        f"🎯 Targets Hit:   `{t['targets_hit']}`\n"
        f"🛑 SL Hit:        `{t['sl_hit']}`\n"
        f"🔔 EOD Closed:    `{t['eod_closed']}`\n"
        f"🏆 Win Rate:      `{win_rate}%`\n"
        f"{pnl_em} Avg P&L:      `{t['avg_pnl_pct']:+.2f}%`\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{trade_lines}"
        f"⏰ EOD Report — {datetime.now(IST).strftime('%I:%M %p IST')}"
    )


# ── Holiday Notice ───────────────────────────────────────────
def send_holiday_notice(reason: str) -> None:
    _post(
        f"🗓 *Market Holiday — {reason}*\n"
        f"Bot sleeping today. No scans will run.\n"
        f"See you tomorrow! 🙏"
    )


# ── Raw warning (used by watchdog) ───────────────────────────
def send_warning(msg: str) -> None:
    _post(msg)


# ── Heartbeat ────────────────────────────────────────────────
def send_heartbeat() -> None:
    _post("💓 ORB Scanner alive")
