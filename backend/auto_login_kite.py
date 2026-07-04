#!/usr/bin/env python3
"""
auto_login_kite.py — Automated Kite Connect daily login.

Runs every morning at 08:00 IST (scheduled via cron).
Performs the full 3-step Kite login:
  1. POST user_id + password
  2. POST TOTP (generated from secret)
  3. Exchange request_token → access_token
  4. Save token to .env
  5. Restart nse_platform_delta container

Usage:
  python auto_login_kite.py

Required .env variables (set these once on the server):
  KITE_USER_ID     — Zerodha Client ID  e.g. AB1234
  KITE_PASSWORD    — Zerodha login password
  KITE_TOTP_SECRET — Base32 TOTP seed (shown when enabling 2FA in Zerodha)
  KITE_API_KEY     — Kite app API key
  KITE_API_SECRET  — Kite app API secret

How to get KITE_TOTP_SECRET:
  In Zerodha > My Account > Security > External TOTP > Setup
  You will see a QR code AND a plain text secret (e.g. JBSWY3DPEHPK3PXP)
  Paste that text string as KITE_TOTP_SECRET — do NOT use the 6-digit code.
"""

import os, re, sys, json, logging, subprocess, time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

try:
    import requests
    import pyotp
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "requests", "pyotp"], check=True)
    import requests
    import pyotp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/kite_autologin.log"),
    ]
)
log = logging.getLogger("kite_autologin")

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

API_KEY     = os.getenv("KITE_API_KEY")
API_SECRET  = os.getenv("KITE_API_SECRET")
USER_ID     = os.getenv("KITE_USER_ID")
PASSWORD    = os.getenv("KITE_PASSWORD")
TOTP_SECRET = os.getenv("KITE_TOTP_SECRET")

BASE = "https://kite.zerodha.com"
LOGIN_URL  = f"{BASE}/api/login"
TWOFA_URL  = f"{BASE}/api/twofa"

JAVA_DELTA_API = "http://127.0.0.1:8090/api/kite/access-token"


def validate_config():
    missing = [k for k, v in {
        "KITE_API_KEY": API_KEY,
        "KITE_API_SECRET": API_SECRET,
        "KITE_USER_ID": USER_ID,
        "KITE_PASSWORD": PASSWORD,
        "KITE_TOTP_SECRET": TOTP_SECRET,
    }.items() if not v]
    if missing:
        log.error("Missing required config: %s", ", ".join(missing))
        log.error("Edit /var/www/html/nse/backend/.env and set these values.")
        sys.exit(1)


def generate_totp() -> str:
    """Generate current TOTP code from the secret."""
    return pyotp.TOTP(TOTP_SECRET.strip().replace(" ", "")).now()


def kite_login() -> str:
    """
    Full Kite Connect login flow.
    Returns the access_token string.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3",
    })

    # ── Step 1: password login ──────────────────────────────────────────────
    log.info("Step 1: password login for user %s", USER_ID)
    resp = session.post(LOGIN_URL, data={
        "user_id": USER_ID,
        "password": PASSWORD,
    })
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != "success":
        raise RuntimeError(f"Login step 1 failed: {body.get('message', body)}")
    request_id = body["data"]["request_id"]
    log.info("Login OK, request_id: %s", request_id)

    # ── Step 2: TOTP ────────────────────────────────────────────────────────
    totp_code = generate_totp()
    log.info("Step 2: TOTP submit (%s)", totp_code)
    resp = session.post(TWOFA_URL, data={
        "user_id": USER_ID,
        "request_id": request_id,
        "twofa_value": totp_code,
        "twofa_type": "totp",
        "skip_session": "",
    }, allow_redirects=False)

    # After 2FA Kite redirects to our callback with request_token in the URL
    redirect_url = resp.headers.get("Location", "")
    log.info("2FA redirect: %s", redirect_url[:120])

    # If the redirect is to our callback, follow it
    if not redirect_url:
        # Some versions return JSON with the redirect
        try:
            body2 = resp.json()
            redirect_url = body2.get("data", {}).get("redirect_url", "")
        except Exception:
            pass

    # Extract request_token from redirect URL
    match = re.search(r"request_token=([A-Za-z0-9]+)", redirect_url)
    if not match:
        # Try following the redirect manually
        resp2 = session.get(redirect_url, allow_redirects=True)
        match = re.search(r"request_token=([A-Za-z0-9]+)", resp2.url)
    if not match:
        raise RuntimeError(f"Could not extract request_token from: {redirect_url}")
    request_token = match.group(1)
    log.info("Got request_token: %s...", request_token[:8])

    # ── Step 3: exchange request_token → access_token ───────────────────────
    import hashlib
    checksum = hashlib.sha256(f"{API_KEY}{request_token}{API_SECRET}".encode()).hexdigest()
    resp = session.post("https://api.kite.trade/session/token", data={
        "api_key": API_KEY,
        "request_token": request_token,
        "checksum": checksum,
    }, headers={"X-Kite-Version": "3"})
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Token exchange failed: {data.get('message', data)}")
    access_token = data["data"]["access_token"]
    user_name    = data["data"].get("user_name", "")
    log.info("Access token obtained for %s (%s)", USER_ID, user_name)
    return access_token


def save_token(token: str):
    """Upsert KITE_ACCESS_TOKEN in the .env file."""
    content = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    if "KITE_ACCESS_TOKEN" in content:
        content = re.sub(r"KITE_ACCESS_TOKEN=.*", f"KITE_ACCESS_TOKEN={token}", content)
    else:
        content = content.rstrip("\n") + f"\nKITE_ACCESS_TOKEN={token}\n"
    ENV_FILE.write_text(content)
    log.info("Token saved to .env")


def notify_services(token: str):
    """Push the new token to the running Java delta service and restart the container."""
    # 1. Update running Java service (no restart needed if it accepts this)
    try:
        r = requests.post(JAVA_DELTA_API, json={"access_token": token}, timeout=5)
        log.info("Java delta service token updated: HTTP %s", r.status_code)
    except Exception as e:
        log.warning("Could not update Java service via API: %s — will restart container", e)

    # 2. Restart the delta Docker container so it picks up the new .env token
    try:
        result = subprocess.run(
            ["docker", "compose", "-f",
             "/var/www/html/nse/docker-compose.yml",
             "restart", "delta"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            log.info("nse_platform_delta restarted successfully")
        else:
            log.warning("Docker restart output: %s", result.stderr)
    except Exception as e:
        log.warning("Could not restart delta container: %s", e)

    # 3. Also update the Python backend settings in memory via env (it reads at startup)
    log.info("Note: Python backend will use new token on next restart.")


def main():
    log.info("=" * 60)
    log.info("Kite auto-login starting — %s IST", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    validate_config()
    try:
        token = kite_login()
        save_token(token)
        notify_services(token)
        log.info("Auto-login complete. Delta feed will start at 09:15 IST.")
        print(f"\n✅ Kite login successful. Access token updated.\n")
    except Exception as e:
        log.error("Auto-login FAILED: %s", e)
        print(f"\n❌ Auto-login failed: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
