#!/usr/bin/env python3
"""
backfill_minute_data.py — Load 90 days of 1-minute OHLCV from Kite into delta_minute_candle.

Usage:
    python3 backfill_minute_data.py [--days 90] [--workers 4]

Notes:
- Historical minute data from Kite does NOT have buy/sell split — those fields are 0.
  The volume field IS loaded and is used for avgSameTimeVolume calculations.
- Rate limit: 3 requests/second (Kite's limit).
- Kite minute API allows max 60 days per request; we split 90 days into 2 chunks.
- Already-existing rows are skipped (upsert by symbol + minute_ts).
"""

import os, sys, time, logging, argparse
from datetime import datetime, date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

try:
    import requests, psycopg2, psycopg2.extras
    from dotenv import load_dotenv
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "requests", "psycopg2-binary", "python-dotenv"], check=True)
    import requests, psycopg2, psycopg2.extras
    from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("/var/log/backfill_minute.log")]
)
log = logging.getLogger("backfill")

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

API_KEY      = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")
DB_HOST      = os.getenv("DB_HOST", "db")   # "db" in Docker, "localhost" on host
DB_PORT      = int(os.getenv("DB_PORT", "5432"))
DB_NAME      = os.getenv("DB_NAME", "nse_stock_db")
DB_USER      = os.getenv("DB_USER", "postgres")
DB_PASS      = os.getenv("DB_PASS", "postgrespassword")

BASE_URL = "https://api.kite.trade"
# Kite rate limit: 3 req/sec for historical — we use a token bucket
_rate_lock = threading.Lock()
_last_req_times = []


def rate_limited_get(url, headers):
    """Enforce 3 req/sec Kite rate limit."""
    global _last_req_times
    with _rate_lock:
        now = time.time()
        # Remove timestamps older than 1 second
        _last_req_times = [t for t in _last_req_times if now - t < 1.0]
        if len(_last_req_times) >= 3:
            sleep_for = 1.0 - (now - _last_req_times[0]) + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)
        _last_req_times.append(time.time())
    return requests.get(url, headers=headers, timeout=30)


def get_db_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS
    )


def get_active_symbols_with_tokens():
    """Get active NSE symbols and their Kite instrument tokens from DB."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Get symbols that have Kite tokens from the instrument registry
            # The delta_minute_candle table uses symbol strings — map via stocks table
            cur.execute("""
                SELECT DISTINCT symbol FROM stocks 
                WHERE is_active = true 
                ORDER BY symbol
            """)
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def get_instrument_token(symbol):
    """Fetch instrument token from Kite instruments CSV (cached in memory)."""
    return _token_cache.get(symbol)


def load_instrument_tokens():
    """Load all NSE EQ instrument tokens from Kite API."""
    log.info("Loading instrument tokens from Kite...")
    import csv, io
    headers = {"X-Kite-Version": "3", "Authorization": f"token {API_KEY}:{ACCESS_TOKEN}"}
    resp = requests.get(f"{BASE_URL}/instruments/NSE", headers=headers, timeout=60)
    resp.raise_for_status()
    tokens = {}
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        if row.get("instrument_type", "").strip() == "EQ" and row.get("segment", "").strip() == "NSE":
            try:
                instrument_token = int(row["instrument_token"].strip())
                tradingsymbol = row["tradingsymbol"].strip()
                tokens[tradingsymbol] = instrument_token
            except (ValueError, KeyError):
                pass
    log.info("Loaded %d NSE EQ instrument tokens", len(tokens))
    return tokens


def get_existing_dates(symbol):
    """Get set of trade_dates already in delta_minute_candle for this symbol."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT trade_date FROM delta_minute_candle WHERE symbol = %s",
                (symbol,)
            )
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def fetch_minute_data(token, symbol, from_date, to_date):
    """Fetch 1-minute OHLCV from Kite historical API."""
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {API_KEY}:{ACCESS_TOKEN}"
    }
    url = (
        f"{BASE_URL}/instruments/historical/{token}/minute"
        f"?from={from_date} 09:00:00&to={to_date} 16:00:00"
        f"&continuous=0&oi=0"
    )
    try:
        resp = rate_limited_get(url, headers)
        if resp.status_code != 200:
            log.warning("%s: HTTP %s — %s", symbol, resp.status_code, resp.text[:100])
            return []
        data = resp.json()
        candles = data.get("data", {}).get("candles", [])
        return candles
    except Exception as e:
        log.error("%s: fetch error — %s", symbol, e)
        return []


def insert_candles(symbol, candles, existing_dates):
    """Insert candle rows into delta_minute_candle, skipping already-loaded dates."""
    if not candles:
        return 0

    rows = []
    for c in candles:
        # Kite candle format: [timestamp, open, high, low, close, volume]
        ts_str = c[0]  # "2026-05-01T09:15:00+0530"
        try:
            # Parse timestamp
            if "+" in ts_str:
                ts_str_clean = ts_str.replace("+0530", "+05:30")
            else:
                ts_str_clean = ts_str
            from datetime import datetime, timezone
            ts = datetime.fromisoformat(ts_str_clean)
            trade_date = ts.date()

            if trade_date in existing_dates:
                continue  # already loaded this day

            open_p  = float(c[1])
            high_p  = float(c[2])
            low_p   = float(c[3])
            close_p = float(c[4])
            volume  = int(c[5])

            rows.append((symbol, trade_date, ts, open_p, high_p, low_p, close_p,
                         volume, 0, 0, 0))  # buy_vol=0, sell_vol=0, delta=0
        except Exception as e:
            log.debug("Parse error for %s candle %s: %s", symbol, c, e)

    if not rows:
        return 0

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO delta_minute_candle
                   (symbol, trade_date, minute_ts, open_price, high_price,
                    low_price, close_price, volume, buy_volume, sell_volume, delta)
                   VALUES %s
                   ON CONFLICT DO NOTHING""",
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                page_size=1000
            )
        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        log.error("%s: insert error — %s", symbol, e)
        return 0
    finally:
        conn.close()


def backfill_symbol(symbol, days):
    """Backfill one symbol: fetch + insert missing minute data."""
    token = get_instrument_token(symbol)
    if token is None:
        return symbol, 0, "no_token"

    existing = get_existing_dates(symbol)
    today = date.today()
    from_date = today - timedelta(days=days)

    # Split into max-60-day chunks (Kite limit for minute data)
    total_inserted = 0
    chunk_start = from_date
    while chunk_start < today:
        chunk_end = min(chunk_start + timedelta(days=59), today - timedelta(days=1))
        candles = fetch_minute_data(token, symbol, chunk_start.isoformat(), chunk_end.isoformat())
        inserted = insert_candles(symbol, candles, existing)
        total_inserted += inserted
        # Update existing_dates to avoid re-checking DB
        for c in candles:
            try:
                ts = datetime.fromisoformat(c[0].replace("+0530", "+05:30"))
                existing.add(ts.date())
            except Exception:
                pass
        chunk_start = chunk_end + timedelta(days=1)

    return symbol, total_inserted, "ok"


def main():
    parser = argparse.ArgumentParser(description="Backfill 1-minute OHLCV from Kite")
    parser.add_argument("--days",    type=int, default=90,  help="Days to backfill (default 90)")
    parser.add_argument("--workers", type=int, default=3,   help="Parallel workers (default 3)")
    parser.add_argument("--symbol",  type=str, default=None, help="Single symbol to test")
    args = parser.parse_args()

    if not ACCESS_TOKEN:
        log.error("KITE_ACCESS_TOKEN not set. Run auto_login_kite.py first.")
        sys.exit(1)

    global _token_cache
    _token_cache = load_instrument_tokens()

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = get_active_symbols_with_tokens()

    log.info("Starting backfill: %d symbols, %d days, %d workers",
             len(symbols), args.days, args.workers)
    start = time.time()

    done = 0
    total_rows = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(backfill_symbol, s, args.days): s for s in symbols}
        for fut in as_completed(futures):
            sym, rows, status = fut.result()
            done += 1
            total_rows += rows
            if status != "ok":
                failed += 1
            if done % 50 == 0 or done == len(symbols):
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(symbols) - done) / rate if rate > 0 else 0
                log.info("[%d/%d] %d rows inserted | %.1f sym/s | ETA %.0fs | failed=%d",
                         done, len(symbols), total_rows, rate, eta, failed)

    elapsed = time.time() - start
    log.info("Done. %d symbols, %d rows inserted in %.0fs", len(symbols), total_rows, elapsed)


_token_cache = {}

if __name__ == "__main__":
    main()
