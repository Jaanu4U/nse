#!/usr/bin/env bash
# watchdog_delta.sh — self-healing monitor for the delta live-feed pipeline.
#
# Run from cron every 5 minutes during market hours (Mon-Fri 09:15–15:30 IST):
#   */5 3-10 * * 1-5 /var/www/html/nse/backend/watchdog_delta.sh >> /var/log/delta_watchdog.log 2>&1
#
# Checks (in order):
#   1. Delta HTTP API responding?         → if not, Docker healthcheck/restart:always handles it; just log.
#   2. Kite authenticated + WS connected? → if not, re-run auto_login_kite.py (pushes fresh token via HTTP).
#   3. Live tick freshness?               → if no ticks for too long, force a reconnect.
#   4. SSE stream serving market data?    → if not, log CRITICAL (needs manual investigation).

set -u
BASE="http://localhost:8090"
TS() { date '+%Y-%m-%d %H:%M:%S'; }

# Only act during market hours (IST)
IST_NOW=$(TZ=Asia/Kolkata date '+%H%M')
IST_DOW=$(TZ=Asia/Kolkata date '+%u')   # 1=Mon … 7=Sun
# 10# forces base-10: values like "0915" would otherwise be parsed as octal and crash
if [[ "$IST_DOW" -gt 5 || "$((10#$IST_NOW))" -lt 915 || "$((10#$IST_NOW))" -gt 1530 ]]; then
    exit 0
fi

# ---- 1. API reachable? ----
STATUS_JSON=$(curl -4 -s --max-time 8 "$BASE/api/kite/status" || true)
if [[ -z "$STATUS_JSON" ]]; then
    echo "[$(TS)] CRITICAL: delta API not responding on 8090 (Docker healthcheck should restart it)"
    exit 1
fi

# ---- 2. Auth + WebSocket connected? ----
AUTH=$(echo "$STATUS_JSON"      | grep -o '"authenticated":[a-z]*'       | cut -d: -f2)
WS=$(echo "$STATUS_JSON"        | grep -o '"websocket_connected":[a-z]*' | cut -d: -f2)

if [[ "$AUTH" != "true" || "$WS" != "true" ]]; then
    echo "[$(TS)] WARN: authenticated=$AUTH websocket=$WS — running auto-login to refresh token"
    cd /var/www/html/nse/backend && python3 auto_login_kite.py
    sleep 20
    # Re-check
    STATUS2=$(curl -4 -s --max-time 8 "$BASE/api/kite/status" || true)
    WS2=$(echo "$STATUS2" | grep -o '"websocket_connected":[a-z]*' | cut -d: -f2)
    if [[ "$WS2" == "true" ]]; then
        echo "[$(TS)] RECOVERED: WebSocket reconnected after token refresh"
    else
        echo "[$(TS)] CRITICAL: WebSocket still down after token refresh — manual action needed"
    fi
    exit 0
fi

# ---- 3. Live tick freshness? ----
LAST_AGE=$(python3 - <<'PY' "$STATUS_JSON"
import json, sys
try:
    print(json.loads(sys.argv[1]).get('last_tick_age_seconds', -1))
except Exception:
    print(-1)
PY
)

if [[ "$LAST_AGE" =~ ^[0-9]+$ ]] && [[ "$LAST_AGE" -ge 180 ]]; then
    echo "[$(TS)] CRITICAL: live feed stale for ${LAST_AGE}s — forcing reconnect"
    curl -4 -s --max-time 8 -X POST "$BASE/api/kite/reconnect" >/dev/null || true
    sleep 10
    STATUS3=$(curl -4 -s --max-time 8 "$BASE/api/kite/status" || true)
    LAST_AGE2=$(python3 - <<'PY' "$STATUS3"
import json, sys
try:
    print(json.loads(sys.argv[1]).get('last_tick_age_seconds', -1))
except Exception:
    print(-1)
PY
)
    if [[ "$LAST_AGE2" =~ ^[0-9]+$ ]] && [[ "$LAST_AGE2" -lt 180 ]]; then
        echo "[$(TS)] RECOVERED: live feed resumed after reconnect"
    else
        echo "[$(TS)] CRITICAL: live feed still stale after reconnect — manual action needed"
    fi
    exit 0
fi

# ---- 4. SSE stream carrying data? ----
SSE_SAMPLE=$(curl -4 -s -N --max-time 5 "$BASE/api/stream/market" 2>/dev/null | grep -m1 '^data:\[' || true)
if [[ -z "$SSE_SAMPLE" || "$SSE_SAMPLE" == 'data:[]' ]]; then
    echo "[$(TS)] WARN: SSE stream empty (no symbols with volume) — check tick flow"
else
    : # healthy — stay silent to keep the log small
fi
