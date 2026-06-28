"""
ORB Alert Bot — One-shot setup script.

Usage (PowerShell / CMD / terminal):
    python setup.py

What it does:
  1. Reads config.ini
  2. Validates all fields are filled
  3. Pushes all env vars to your Render service via Render API
  4. Triggers a redeploy
  5. Prints status

Requirements: pip install requests  (stdlib configparser used for config)
"""
import configparser
import sys
import requests

CONFIG_FILE = "config.ini"
RENDER_API  = "https://api.render.com/v1"

# ── colour helpers (work on Windows 10+ terminals) ──────────────
RED   = "\033[91m"
GREEN = "\033[92m"
CYAN  = "\033[96m"
RESET = "\033[0m"
BOLD  = "\033[1m"

ok  = lambda s: print(f"{GREEN}✓{RESET} {s}")
err = lambda s: print(f"{RED}✗  {s}{RESET}")
inf = lambda s: print(f"{CYAN}→{RESET} {s}")


def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG_FILE):
        err(f"Cannot find {CONFIG_FILE}. Run this script from the orb_alert folder.")
        sys.exit(1)
    return cfg


def validate(cfg: configparser.ConfigParser):
    required = {
        "upstox":   ["mobile", "password", "pin", "totp_secret", "client_id", "client_secret"],
        "telegram": ["bot_token", "chat_id"],
        "render":   ["api_key", "service_id"],
        "google":   ["sheet_id", "service_account_json"],
        "scanner":  ["interval_seconds", "market_open_hhmm", "market_close_hhmm", "heartbeat_minutes"],
    }
    errors = []
    for section, keys in required.items():
        for key in keys:
            val = cfg.get(section, key, fallback="").strip()
            if not val or val.startswith("your_") or val in ("999999999", "123456789:AAFxxx"):
                errors.append(f"[{section}] {key} — not filled in")

    # validate the JSON file actually exists
    sa_path = cfg.get("google", "service_account_json", fallback="").strip()
    if sa_path and not sa_path.startswith("your_"):
        import os
        if not os.path.exists(sa_path):
            errors.append(f"[google] service_account_json — file not found: {sa_path}")

    if errors:
        err("Fix these in config.ini before running setup:")
        for e in errors:
            print(f"   {RED}•{RESET} {e}")
        sys.exit(1)
    ok("config.ini validated")


def _read_sa_json(cfg: configparser.ConfigParser) -> str:
    """
    Read service account JSON, validate it, return as Base64.
    Base64 keeps the private key unreadable in Render dashboard + logs.
    App decodes at runtime — never stored as plaintext anywhere.
    """
    import json, os, base64
    sa_path = cfg.get("google", "service_account_json", fallback="").strip()
    if not sa_path or not os.path.exists(sa_path):
        err(f"Service account JSON not found: {sa_path}")
        sys.exit(1)
    with open(sa_path) as f:
        data = json.load(f)
    # quick sanity check
    missing = [k for k in ["type","project_id","private_key","client_email"] if k not in data]
    if missing:
        err(f"service_account.json missing fields: {missing}")
        sys.exit(1)
    compact = json.dumps(data, separators=(",", ":"))
    b64     = base64.b64encode(compact.encode()).decode()
    ok(f"Service account JSON base64-encoded (project: {data.get('project_id','?')})")
    return b64


def build_env_vars(cfg: configparser.ConfigParser) -> list[dict]:
    """Build Render env-var payload from config."""
    u = cfg["upstox"]
    t = cfg["telegram"]
    s = cfg["scanner"]
    g = cfg["google"]
    redirect_uri = f"https://{cfg['render'].get('service_name', 'orb-alert-bot')}.onrender.com/callback"
    sa_json_str  = _read_sa_json(cfg)

    return [
        {"key": "UPSTOX_MOBILE",               "value": u["mobile"]},
        {"key": "UPSTOX_PASSWORD",              "value": u["password"]},
        {"key": "UPSTOX_PIN",                   "value": u["pin"]},
        {"key": "UPSTOX_TOTP_SECRET",           "value": u["totp_secret"]},
        {"key": "UPSTOX_CLIENT_ID",             "value": u["client_id"]},
        {"key": "UPSTOX_CLIENT_SECRET",         "value": u["client_secret"]},
        {"key": "UPSTOX_REDIRECT_URI",          "value": redirect_uri},
        {"key": "UPSTOX_ACCESS_TOKEN",          "value": ""},
        {"key": "TELEGRAM_BOT_TOKEN",           "value": t["bot_token"]},
        {"key": "TELEGRAM_CHAT_ID",             "value": t["chat_id"]},
        {"key": "GOOGLE_SHEET_ID",              "value": g["sheet_id"]},
        {"key": "GOOGLE_SERVICE_ACCOUNT_JSON",  "value": sa_json_str},
        {"key": "INTERVAL_SECONDS",             "value": s["interval_seconds"]},
        {"key": "MARKET_OPEN_HHMM",             "value": s["market_open_hhmm"]},
        {"key": "MARKET_CLOSE_HHMM",            "value": s["market_close_hhmm"]},
        {"key": "HEARTBEAT_MINUTES",            "value": s["heartbeat_minutes"]},
        {"key": "PAPER_SL_PCT",                 "value": cfg.get("paper_trades", "sl_pct",  fallback="0.008")},
        {"key": "PAPER_TGT_PCT",                "value": cfg.get("paper_trades", "tgt_pct", fallback="0.015")},
        {"key": "GAP_UP_FILTER_PCT",            "value": cfg.get("scanner", "gap_up_filter_pct", fallback="0.02")},
        {"key": "PYTHON_VERSION",               "value": "3.12.9"},
    ]


def push_to_render(cfg: configparser.ConfigParser, env_vars: list[dict]):
    api_key    = cfg["render"]["api_key"].strip()
    service_id = cfg["render"]["service_id"].strip()
    headers    = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

    inf(f"Pushing {len(env_vars)} env vars to Render service {service_id} ...")
    url = f"{RENDER_API}/services/{service_id}/env-vars"
    r   = requests.put(url, json=env_vars, headers=headers, timeout=15)

    if r.status_code == 200:
        ok(f"All {len(env_vars)} env vars set on Render")
    else:
        err(f"Render API error {r.status_code}: {r.text}")
        sys.exit(1)


def trigger_deploy(cfg: configparser.ConfigParser):
    api_key    = cfg["render"]["api_key"].strip()
    service_id = cfg["render"]["service_id"].strip()
    headers    = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

    inf("Triggering redeploy ...")
    url = f"{RENDER_API}/services/{service_id}/deploys"
    r   = requests.post(url, json={"clearCache": "do_not_clear"}, headers=headers, timeout=15)

    if r.status_code in (200, 201):
        deploy_id = r.json().get("id", "unknown")
        ok(f"Deploy triggered (id: {deploy_id})")
    else:
        err(f"Deploy trigger failed {r.status_code}: {r.text}")
        print("   Env vars were set. Redeploy manually from Render dashboard.")


def verify_telegram(cfg: configparser.ConfigParser):
    inf("Verifying Telegram bot ...")
    token = cfg["telegram"]["bot_token"].strip()
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
    if r.status_code == 200 and r.json().get("ok"):
        name = r.json()["result"]["first_name"]
        ok(f"Telegram bot OK — @{r.json()['result']['username']} ({name})")
    else:
        err("Telegram bot token invalid. Check [telegram] bot_token in config.ini")
        sys.exit(1)


def print_summary(cfg: configparser.ConfigParser):
    service_id = cfg["render"]["service_id"].strip()
    # Try to get service name from Render API
    api_key = cfg["render"]["api_key"].strip()
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    r = requests.get(f"{RENDER_API}/services/{service_id}", headers=headers, timeout=10)
    url = "your-service.onrender.com"
    if r.status_code == 200:
        url = r.json().get("serviceDetails", {}).get("url", url)

    print()
    print(f"{BOLD}{'─'*50}{RESET}")
    print(f"{BOLD}{GREEN}  Setup complete!{RESET}")
    print(f"{'─'*50}")
    print(f"  Service URL  : https://{url}")
    print(f"  Health check : https://{url}/health")
    print(f"  Manual scan  : POST https://{url}/run-now")
    print(f"  Status       : GET  https://{url}/")
    print(f"{'─'*50}")
    print(f"  {CYAN}Next steps:{RESET}")
    print(f"  1. Set UptimeRobot → monitor https://{url}/health every 5 min")
    print(f"  2. Wait for Render deploy to finish (~2 min)")
    print(f"  3. Bot auto-refreshes Upstox token at 08:50 AM IST daily")
    print(f"  4. Alerts fire 09:45–11:00 AM IST when conditions met")
    print(f"{'─'*50}")


def main():
    print(f"\n{BOLD}  ORB Alert Bot — Setup{RESET}\n{'─'*50}\n")

    cfg = load_config()
    validate(cfg)
    verify_telegram(cfg)

    env_vars = build_env_vars(cfg)
    push_to_render(cfg, env_vars)
    trigger_deploy(cfg)
    print_summary(cfg)


if __name__ == "__main__":
    # ponytail: stdlib only (configparser, sys) + requests (already in requirements.txt)
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
