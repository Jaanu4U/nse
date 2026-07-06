"""
regime_service.py — Fetch India VIX from Yahoo Finance and compute regime tags.

Regime tags per trading day:
  - india_vix          : VIX value (Yahoo ^INDIAVIX)
  - vix_percentile_20d : VIX vs last 20 trading days (0-100)
  - vix_level          : LOW (<25th pct) / NORMAL / HIGH (>75th) / EXTREME (>90th)
  - is_expiry_day      : Every Thursday = weekly expiry; last Thursday of month = monthly
  - expiry_type        : WEEKLY / MONTHLY / None
  - is_gap_day         : Market open gap vs prev close > 0.75%
  - gap_pct            : Actual gap %
  - nifty_trend        : UP / DOWN / FLAT (based on NIFTY prev close vs close 5 days ago)
  - regime_note        : Human-readable summary e.g. "High VIX + Monthly Expiry"
"""

import logging
import os
from datetime import date, timedelta
import calendar

log = logging.getLogger("regime_service")


def _is_expiry_day(d: date):
    """
    NSE F&O expiry rules:
      - Every Thursday = Weekly expiry
      - Last Thursday of month = Monthly expiry (takes precedence)
    Returns (is_expiry, expiry_type)
    """
    if d.weekday() != 3:  # 3 = Thursday
        return False, None
    # Find last Thursday of the month
    last_day = calendar.monthrange(d.year, d.month)[1]
    last_thursday = max(
        date(d.year, d.month, day)
        for day in range(last_day, last_day - 7, -1)
        if date(d.year, d.month, day).weekday() == 3
    )
    if d == last_thursday:
        return True, "MONTHLY"
    return True, "WEEKLY"


def _fetch_vix_history(days=25):
    """Fetch last N days of India VIX from Yahoo Finance. Returns list of floats."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("^INDIAVIX")
        hist = ticker.history(period=f"{days + 10}d")
        if hist.empty:
            return []
        return hist["Close"].dropna().tolist()[-days:]
    except Exception as e:
        log.warning("VIX fetch from Yahoo failed: %s", e)
        return []


def _get_gap_pct(conn, trade_date: date) -> float:
    """
    Calculate NIFTY50 gap = (today's first candle open - yesterday's last close) / yesterday's last close * 100
    Uses delta_minute_candle for a broad market proxy (NIFTY50 index or top liquid stocks).
    Falls back to None if data unavailable.
    """
    try:
        prev_date = trade_date - timedelta(days=1)
        # Walk back up to 5 days to find previous trading day
        for i in range(1, 6):
            prev_date = trade_date - timedelta(days=i)
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT AVG(close_price) FROM delta_minute_candle
                    WHERE trade_date = %s
                    AND EXTRACT(HOUR FROM minute_ts AT TIME ZONE 'Asia/Kolkata') = 15
                    AND EXTRACT(MINUTE FROM minute_ts AT TIME ZONE 'Asia/Kolkata') >= 25
                    LIMIT 1
                """, (prev_date,))
                row = cur.fetchone()
                if row and row[0]:
                    prev_close = float(row[0])
                    break
        else:
            return 0.0

        with conn.cursor() as cur:
            cur.execute("""
                SELECT AVG(open_price) FROM delta_minute_candle
                WHERE trade_date = %s
                AND EXTRACT(HOUR FROM minute_ts AT TIME ZONE 'Asia/Kolkata') = 9
                AND EXTRACT(MINUTE FROM minute_ts AT TIME ZONE 'Asia/Kolkata') = 15
            """, (trade_date,))
            row = cur.fetchone()
            if not row or not row[0]:
                return 0.0
            today_open = float(row[0])

        return round((today_open - prev_close) / prev_close * 100, 4)
    except Exception as e:
        log.warning("Gap calculation failed: %s", e)
        return 0.0


def _get_nifty_trend(conn, trade_date: date) -> str:
    """Compare avg close price today vs 5 trading days ago."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT trade_date, AVG(close_price) AS avg_close
                FROM delta_minute_candle
                WHERE trade_date >= %s - INTERVAL '10 days'
                  AND trade_date <= %s
                  AND EXTRACT(HOUR FROM minute_ts AT TIME ZONE 'Asia/Kolkata') = 15
                  AND EXTRACT(MINUTE FROM minute_ts AT TIME ZONE 'Asia/Kolkata') >= 25
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT 6
            """, (trade_date, trade_date))
            rows = cur.fetchall()
        if len(rows) < 2:
            return "FLAT"
        today_close = float(rows[0][1])
        old_close   = float(rows[-1][1])
        chg = (today_close - old_close) / old_close * 100
        if chg > 0.5:
            return "UP"
        elif chg < -0.5:
            return "DOWN"
        return "FLAT"
    except Exception as e:
        log.warning("Nifty trend calc failed: %s", e)
        return "FLAT"


def update_regime_context(trade_date: date = None):
    """
    Main entry point — compute and upsert regime context for trade_date (default: today).
    Called daily after market close by the scheduler.
    """
    import psycopg2
    from dotenv import load_dotenv
    from pathlib import Path

    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "db"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "nse_stock_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASS", "postgrespassword"),
    )

    if trade_date is None:
        trade_date = date.today()

    try:
        # 1. India VIX from Yahoo Finance
        vix_history = _fetch_vix_history(days=22)
        india_vix = vix_history[-1] if vix_history else None
        vix_percentile = None
        vix_level = "NORMAL"

        if india_vix and len(vix_history) >= 5:
            sorted_vix = sorted(vix_history[:-1])  # exclude today
            rank = sum(1 for v in sorted_vix if v <= india_vix)
            vix_percentile = round(rank / len(sorted_vix) * 100, 2)
            # Minor fix: keep NORMAL until we have at least 10 days of history
            # to avoid percentile instability with sparse early data.
            if len(sorted_vix) < 10:
                vix_level = "NORMAL"
                log.info("VIX history < 10 days (%d) — clamping level to NORMAL", len(sorted_vix))
            elif vix_percentile >= 90:
                vix_level = "EXTREME"
            elif vix_percentile >= 75:
                vix_level = "HIGH"
            elif vix_percentile <= 25:
                vix_level = "LOW"
            else:
                vix_level = "NORMAL"

        # 2. Expiry day
        is_expiry, expiry_type = _is_expiry_day(trade_date)

        # 3. Gap day
        gap_pct = _get_gap_pct(conn, trade_date)
        is_gap_day = abs(gap_pct) >= 0.75

        # 4. NIFTY trend
        nifty_trend = _get_nifty_trend(conn, trade_date)

        # 5. Regime note
        notes = []
        if india_vix:
            notes.append(f"VIX={india_vix:.1f}({vix_level})")
        if is_expiry:
            notes.append(f"{expiry_type} expiry")
        if is_gap_day:
            notes.append(f"Gap={gap_pct:+.2f}%")
        if nifty_trend != "FLAT":
            notes.append(f"Nifty {nifty_trend}")
        regime_note = " | ".join(notes) if notes else "Normal day"

        # 6. Upsert
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO delta_regime_context
                  (trade_date, india_vix, vix_percentile_20d, vix_level,
                   is_expiry_day, expiry_type, is_gap_day, gap_pct,
                   nifty_trend, regime_note, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (trade_date) DO UPDATE SET
                  india_vix          = EXCLUDED.india_vix,
                  vix_percentile_20d = EXCLUDED.vix_percentile_20d,
                  vix_level          = EXCLUDED.vix_level,
                  is_expiry_day      = EXCLUDED.is_expiry_day,
                  expiry_type        = EXCLUDED.expiry_type,
                  is_gap_day         = EXCLUDED.is_gap_day,
                  gap_pct            = EXCLUDED.gap_pct,
                  nifty_trend        = EXCLUDED.nifty_trend,
                  regime_note        = EXCLUDED.regime_note,
                  updated_at         = NOW()
            """, (trade_date, india_vix, vix_percentile, vix_level,
                  is_expiry, expiry_type, is_gap_day, gap_pct,
                  nifty_trend, regime_note))
        conn.commit()

        log.info("[REGIME] %s → VIX=%.2f(%s pct=%s) expiry=%s gap=%+.2f%% trend=%s note: %s",
                 trade_date, india_vix or 0, vix_level, vix_percentile,
                 expiry_type or "No", gap_pct, nifty_trend, regime_note)
        return {
            "trade_date": str(trade_date),
            "india_vix": india_vix,
            "vix_level": vix_level,
            "vix_percentile_20d": vix_percentile,
            "is_expiry_day": is_expiry,
            "expiry_type": expiry_type,
            "is_gap_day": is_gap_day,
            "gap_pct": gap_pct,
            "nifty_trend": nifty_trend,
            "regime_note": regime_note,
        }
    finally:
        conn.close()
