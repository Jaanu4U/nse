#!/usr/bin/env python3
"""
train_delta_models.py — Build Level 2 (historical stats) and Level 3 (XGBoost ML)
models from delta_minute_candle data.

Run:
    python3 train_delta_models.py [--level 2|3|all] [--days 60]

Level 2: Per-stock historical statistics
  - Up/down probability by volume bucket
  - Average price move after high-volume events
  - Volume impact coefficient
  - Time-of-day behavior

Level 3: XGBoost global probability model
  - Features: volume_ratio, price_vs_open, candle_direction, time, momentum
  - Target: will price move +0.3% in next 15 minutes?
  - Trained on all stocks (global model)
  - Calibrated probabilities
"""

import os, sys, json, logging, argparse, pickle
from datetime import datetime, date, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler("/var/log/train_delta_models.log")]
)
log = logging.getLogger("train_delta_models")

try:
    import numpy as np
    import pandas as pd
    import psycopg2, psycopg2.extras
    from dotenv import load_dotenv
except ImportError:
    log.error("Missing packages — run inside backend container: sudo docker exec nse_platform_backend python3 /workspace/train_delta_models.py")
    sys.exit(1)

load_dotenv(Path(__file__).parent / ".env")

DB_CONFIG = dict(
    host=os.getenv("DB_HOST", "db"),
    port=int(os.getenv("DB_PORT", 5432)),
    dbname=os.getenv("DB_NAME", "nse_stock_db"),
    user=os.getenv("DB_USER", "postgres"),
    password=os.getenv("DB_PASS", "postgrespassword"),
)
MODEL_DIR = Path("/workspace/models")


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL 2 — Historical statistics per stock
# ─────────────────────────────────────────────────────────────────────────────

LEVEL2_SQL = """
WITH avg_vols AS (
    -- 20-day avg volume per symbol per minute-of-day
    SELECT symbol,
           EXTRACT(HOUR FROM minute_ts AT TIME ZONE 'Asia/Kolkata')::int * 60
             + EXTRACT(MINUTE FROM minute_ts AT TIME ZONE 'Asia/Kolkata')::int AS mod,
           AVG(volume) AS avg_vol
    FROM delta_minute_candle
    WHERE trade_date >= CURRENT_DATE - INTERVAL '25 days'
      AND trade_date <  CURRENT_DATE
      AND volume > 0
    GROUP BY symbol, mod
),
base AS (
    SELECT
        d.symbol,
        d.minute_ts,
        d.close_price AS price_now,
        d.volume,
        av.avg_vol,
        CASE WHEN av.avg_vol > 0 THEN d.volume::float / av.avg_vol ELSE 1 END AS vol_ratio,
        EXTRACT(HOUR FROM d.minute_ts AT TIME ZONE 'Asia/Kolkata')::int AS ist_hour,
        n15.close_price AS price_15m,
        CASE WHEN d.close_price > 0 THEN
            (n15.close_price - d.close_price) / d.close_price * 100
        ELSE NULL END AS move_15m
    FROM delta_minute_candle d
    JOIN avg_vols av ON av.symbol = d.symbol
        AND av.mod = EXTRACT(HOUR FROM d.minute_ts AT TIME ZONE 'Asia/Kolkata')::int * 60
                   + EXTRACT(MINUTE FROM d.minute_ts AT TIME ZONE 'Asia/Kolkata')::int
    JOIN delta_minute_candle n15 ON n15.symbol = d.symbol
        AND n15.minute_ts = d.minute_ts + INTERVAL '15 minutes'
    WHERE d.trade_date >= CURRENT_DATE - INTERVAL '{days} days'
      AND d.trade_date <  CURRENT_DATE
      AND d.volume > 0
      AND d.close_price > 0
      AND n15.close_price > 0
)
SELECT
    symbol,
    COUNT(*) AS samples,
    -- Overall
    ROUND(AVG(CASE WHEN move_15m > 0 THEN 1.0 ELSE 0.0 END) * 100, 2) AS up_prob_overall,
    ROUND(AVG(move_15m), 4) AS avg_move_15m,
    -- High volume (> 2x avg)
    ROUND(AVG(CASE WHEN vol_ratio > 2 AND move_15m > 0 THEN 1.0
                   WHEN vol_ratio > 2 THEN 0.0 ELSE NULL END) * 100, 2) AS up_prob_high_vol,
    ROUND(AVG(CASE WHEN vol_ratio > 2 THEN move_15m ELSE NULL END), 4) AS avg_move_high_vol,
    SUM(CASE WHEN vol_ratio > 2 THEN 1 ELSE 0 END) AS samples_high_vol,
    -- Impact coefficient (move% per unit vol_ratio above 1)
    ROUND(
        COALESCE(
            REGR_SLOPE(move_15m, vol_ratio)
        , 0)::numeric, 5
    ) AS impact_coeff,
    -- Morning (9:15-11:30) vs afternoon (11:30-15:30)
    ROUND(AVG(CASE WHEN ist_hour < 11 AND move_15m > 0 THEN 1.0
                   WHEN ist_hour < 11 THEN 0.0 ELSE NULL END) * 100, 2) AS up_prob_morning,
    ROUND(AVG(CASE WHEN ist_hour >= 11 AND move_15m > 0 THEN 1.0
                   WHEN ist_hour >= 11 THEN 0.0 ELSE NULL END) * 100, 2) AS up_prob_afternoon,
    -- Volume bucket stats as arrays [weak, normal, strong, very_strong, extreme]
    jsonb_build_object(
        'weak',       jsonb_build_object(
            'up_prob', ROUND(AVG(CASE WHEN vol_ratio < 1 AND move_15m > 0 THEN 1.0 WHEN vol_ratio < 1 THEN 0.0 ELSE NULL END) * 100, 1),
            'samples', SUM(CASE WHEN vol_ratio < 1 THEN 1 ELSE 0 END)),
        'normal',     jsonb_build_object(
            'up_prob', ROUND(AVG(CASE WHEN vol_ratio BETWEEN 1 AND 2 AND move_15m > 0 THEN 1.0 WHEN vol_ratio BETWEEN 1 AND 2 THEN 0.0 ELSE NULL END) * 100, 1),
            'avg_move', ROUND(AVG(CASE WHEN vol_ratio BETWEEN 1 AND 2 THEN move_15m ELSE NULL END), 3),
            'samples', SUM(CASE WHEN vol_ratio BETWEEN 1 AND 2 THEN 1 ELSE 0 END)),
        'strong',     jsonb_build_object(
            'up_prob', ROUND(AVG(CASE WHEN vol_ratio BETWEEN 2 AND 3 AND move_15m > 0 THEN 1.0 WHEN vol_ratio BETWEEN 2 AND 3 THEN 0.0 ELSE NULL END) * 100, 1),
            'avg_move', ROUND(AVG(CASE WHEN vol_ratio BETWEEN 2 AND 3 THEN move_15m ELSE NULL END), 3),
            'samples', SUM(CASE WHEN vol_ratio BETWEEN 2 AND 3 THEN 1 ELSE 0 END)),
        'very_strong', jsonb_build_object(
            'up_prob', ROUND(AVG(CASE WHEN vol_ratio > 3 AND move_15m > 0 THEN 1.0 WHEN vol_ratio > 3 THEN 0.0 ELSE NULL END) * 100, 1),
            'avg_move', ROUND(AVG(CASE WHEN vol_ratio > 3 THEN move_15m ELSE NULL END), 3),
            'samples', SUM(CASE WHEN vol_ratio > 3 THEN 1 ELSE 0 END))
    ) AS bucket_stats
FROM base
GROUP BY symbol
HAVING COUNT(*) >= 100
"""


def build_level2(days=60):
    log.info("Building Level 2 historical profiles (last %d days)...", days)
    conn = get_conn()
    try:
        sql = LEVEL2_SQL.replace("{days}", str(days))
        df = pd.read_sql(sql, conn)
        log.info("Computed Level 2 for %d symbols", len(df))

        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO delta_stock_profiles
                   (symbol, samples, up_prob_overall, avg_move_15m,
                    up_prob_high_vol, avg_move_high_vol, samples_high_vol,
                    impact_coeff, bucket_stats, up_prob_morning, up_prob_afternoon)
                   VALUES %s
                   ON CONFLICT (symbol) DO UPDATE SET
                     samples = EXCLUDED.samples,
                     up_prob_overall = EXCLUDED.up_prob_overall,
                     avg_move_15m = EXCLUDED.avg_move_15m,
                     up_prob_high_vol = EXCLUDED.up_prob_high_vol,
                     avg_move_high_vol = EXCLUDED.avg_move_high_vol,
                     samples_high_vol = EXCLUDED.samples_high_vol,
                     impact_coeff = EXCLUDED.impact_coeff,
                     bucket_stats = EXCLUDED.bucket_stats,
                     up_prob_morning = EXCLUDED.up_prob_morning,
                     up_prob_afternoon = EXCLUDED.up_prob_afternoon,
                     updated_at = NOW()""",
                [
                    (
                        r.symbol, int(r.samples) if r.samples else 0,
                        float(r.up_prob_overall) if r.up_prob_overall else None,
                        float(r.avg_move_15m) if r.avg_move_15m else None,
                        float(r.up_prob_high_vol) if r.up_prob_high_vol else None,
                        float(r.avg_move_high_vol) if r.avg_move_high_vol else None,
                        int(r.samples_high_vol) if r.samples_high_vol else 0,
                        float(r.impact_coeff) if r.impact_coeff else None,
                        json.dumps(r.bucket_stats) if r.bucket_stats else None,
                        float(r.up_prob_morning) if r.up_prob_morning else None,
                        float(r.up_prob_afternoon) if r.up_prob_afternoon else None,
                    )
                    for r in df.itertuples()
                ],
                page_size=200
            )
        conn.commit()
        log.info("Level 2 saved for %d symbols", len(df))
        return df
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL 3 — XGBoost ML model
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_SQL = """
WITH avg_vols AS (
    SELECT symbol,
           EXTRACT(HOUR FROM minute_ts AT TIME ZONE 'Asia/Kolkata')::int * 60
             + EXTRACT(MINUTE FROM minute_ts AT TIME ZONE 'Asia/Kolkata')::int AS mod,
           AVG(volume) AS avg_vol
    FROM delta_minute_candle
    WHERE trade_date >= CURRENT_DATE - INTERVAL '{days} days'
      AND trade_date <  CURRENT_DATE
      AND volume > 0
    GROUP BY symbol, mod
),
daily_open AS (
    SELECT symbol, trade_date, MIN(open_price) AS day_open
    FROM delta_minute_candle
    WHERE EXTRACT(HOUR FROM minute_ts AT TIME ZONE 'Asia/Kolkata') = 9
      AND EXTRACT(MINUTE FROM minute_ts AT TIME ZONE 'Asia/Kolkata') = 15
    GROUP BY symbol, trade_date
),
prev_min AS (
    SELECT symbol, minute_ts, close_price AS prev_close
    FROM delta_minute_candle
),
base AS (
    SELECT
        d.symbol,
        d.trade_date,
        d.minute_ts,
        -- Time features
        EXTRACT(HOUR FROM d.minute_ts AT TIME ZONE 'Asia/Kolkata')::int AS ist_hour,
        EXTRACT(MINUTE FROM d.minute_ts AT TIME ZONE 'Asia/Kolkata')::int AS ist_minute,
        EXTRACT(DOW FROM d.trade_date)::int AS day_of_week,
        -- Price features
        CASE WHEN do_.day_open > 0 THEN (d.close_price - do_.day_open) / do_.day_open * 100 ELSE 0 END AS price_vs_open,
        CASE WHEN d.open_price > 0 THEN (d.close_price - d.open_price) / d.open_price * 100 ELSE 0 END AS candle_body_pct,
        CASE WHEN d.high_price > d.low_price THEN
            (d.high_price - GREATEST(d.open_price, d.close_price)) / (d.high_price - d.low_price) ELSE 0
        END AS upper_wick_ratio,
        CASE WHEN d.high_price > d.low_price THEN
            (LEAST(d.open_price, d.close_price) - d.low_price) / (d.high_price - d.low_price) ELSE 0
        END AS lower_wick_ratio,
        -- Volume features
        CASE WHEN av.avg_vol > 0 THEN d.volume::float / av.avg_vol ELSE 1 END AS vol_ratio,
        -- Momentum (prev minute price change)
        CASE WHEN pm.prev_close > 0 THEN (d.close_price - pm.prev_close) / pm.prev_close * 100 ELSE 0 END AS prev_1m_move,
        -- Target: price move in next 15 minutes
        CASE WHEN d.close_price > 0 THEN
            (n15.close_price - d.close_price) / d.close_price * 100
        ELSE NULL END AS move_15m
    FROM delta_minute_candle d
    JOIN avg_vols av ON av.symbol = d.symbol
        AND av.mod = EXTRACT(HOUR FROM d.minute_ts AT TIME ZONE 'Asia/Kolkata')::int * 60
                   + EXTRACT(MINUTE FROM d.minute_ts AT TIME ZONE 'Asia/Kolkata')::int
    LEFT JOIN daily_open do_ ON do_.symbol = d.symbol AND do_.trade_date = d.trade_date
    LEFT JOIN prev_min pm ON pm.symbol = d.symbol
        AND pm.minute_ts = d.minute_ts - INTERVAL '1 minute'
    JOIN delta_minute_candle n15 ON n15.symbol = d.symbol
        AND n15.minute_ts = d.minute_ts + INTERVAL '15 minutes'
    WHERE d.trade_date >= CURRENT_DATE - INTERVAL '{days} days'
      AND d.trade_date <  CURRENT_DATE
      AND d.volume > 0
      AND d.close_price > 0
      AND n15.close_price > 0
)
SELECT * FROM base
WHERE ist_hour >= 9 AND ist_hour < 15
  AND move_15m IS NOT NULL
"""

FEATURE_COLS = [
    'ist_hour', 'ist_minute', 'day_of_week',
    'price_vs_open', 'candle_body_pct',
    'upper_wick_ratio', 'lower_wick_ratio',
    'vol_ratio', 'prev_1m_move',
]

TARGET_UP   = 0.3   # +0.3% in 15 min = bullish
TARGET_DOWN = -0.3  # -0.3% in 15 min = bearish


def build_level3(days=60):
    try:
        from xgboost import XGBClassifier
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score, classification_report
    except ImportError:
        log.error("xgboost/sklearn not available. Install: pip install xgboost scikit-learn")
        return

    log.info("Loading feature data for Level 3 (last %d days)...", days)
    conn = get_conn()
    try:
        sql = FEATURES_SQL.replace("{days}", str(days))
        df = pd.read_sql(sql, conn)
    finally:
        conn.close()

    log.info("Loaded %d rows for Level 3 training", len(df))
    if len(df) < 10000:
        log.warning("Too few samples (%d). Skipping Level 3.", len(df))
        return

    # Build features and targets
    X = df[FEATURE_COLS].fillna(0).clip(-10, 10)
    # Binary target: price up at least +TARGET_UP%
    y_up = (df['move_15m'] > TARGET_UP).astype(int)

    # Time-based train/test split (last 10% of dates = test)
    dates = sorted(df['trade_date'].unique())
    split_idx = int(len(dates) * 0.85)
    test_dates = set(dates[split_idx:])
    train_mask = ~df['trade_date'].isin(test_dates)
    test_mask  =  df['trade_date'].isin(test_dates)

    X_train, X_test = X[train_mask], X[test_mask]
    y_train, y_test = y_up[train_mask], y_up[test_mask]

    log.info("Train: %d rows | Test: %d rows | Positive rate: %.1f%%",
             len(X_train), len(X_test), y_up.mean() * 100)

    # Train XGBoost
    model = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        use_label_encoder=False,
        eval_metric='logloss',
        n_jobs=-1,
        random_state=42,
        verbosity=0,
    )

    log.info("Training XGBoost model...")
    model.fit(X_train, y_train,
              eval_set=[(X_test, y_test)],
              verbose=False)

    # Calibrate probabilities
    calibrated = CalibratedClassifierCV(model, method='isotonic', cv='prefit')
    calibrated.fit(X_test, y_test)

    # Evaluate
    y_prob = calibrated.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, y_prob)
    log.info("Test AUC: %.4f | Positive rate: %.1f%%", auc, y_test.mean() * 100)

    # Feature importance
    importance = dict(zip(FEATURE_COLS, model.feature_importances_.tolist()))
    importance_sorted = dict(sorted(importance.items(), key=lambda x: -x[1]))
    log.info("Feature importance: %s", json.dumps({k: round(v, 4) for k, v in list(importance_sorted.items())[:5]}))

    # Save model
    MODEL_DIR.mkdir(exist_ok=True)
    model_path = MODEL_DIR / "delta_xgb_model.pkl"
    with open(model_path, 'wb') as f:
        pickle.dump({'model': calibrated, 'features': FEATURE_COLS,
                     'auc': auc, 'trained_at': datetime.now().isoformat(),
                     'importance': importance_sorted}, f)
    log.info("Model saved to %s", model_path)

    # Generate predictions for all active stocks using latest day's data
    log.info("Generating current predictions for all stocks...")
    generate_ml_predictions(calibrated, importance_sorted)

    return calibrated


def generate_ml_predictions(model, importance):
    """Score each symbol using its most recent available minute candles."""
    conn = get_conn()
    try:
        sql = """
        WITH avg_vols AS (
            SELECT symbol,
                   EXTRACT(HOUR FROM minute_ts AT TIME ZONE 'Asia/Kolkata')::int * 60
                     + EXTRACT(MINUTE FROM minute_ts AT TIME ZONE 'Asia/Kolkata')::int AS mod,
                   AVG(volume) AS avg_vol
            FROM delta_minute_candle
            WHERE trade_date >= CURRENT_DATE - INTERVAL '22 days'
            GROUP BY symbol, mod
        ),
        latest AS (
            SELECT DISTINCT ON (symbol) d.*,
                CASE WHEN av.avg_vol > 0 THEN d.volume::float / av.avg_vol ELSE 1 END AS vol_ratio
            FROM delta_minute_candle d
            JOIN avg_vols av ON av.symbol = d.symbol
                AND av.mod = EXTRACT(HOUR FROM d.minute_ts AT TIME ZONE 'Asia/Kolkata')::int * 60
                            + EXTRACT(MINUTE FROM d.minute_ts AT TIME ZONE 'Asia/Kolkata')::int
            WHERE d.volume > 0 AND d.close_price > 0
            ORDER BY d.symbol, d.minute_ts DESC
        ),
        prev_min AS (
            SELECT DISTINCT ON (d.symbol) d.symbol,
                CASE WHEN pm.close_price > 0 THEN
                    (d.close_price - pm.close_price) / pm.close_price * 100
                ELSE 0 END AS prev_1m_move
            FROM delta_minute_candle d
            JOIN delta_minute_candle pm ON pm.symbol = d.symbol
                AND pm.minute_ts = d.minute_ts - INTERVAL '1 minute'
            WHERE d.volume > 0 ORDER BY d.symbol, d.minute_ts DESC
        ),
        day_open AS (
            SELECT DISTINCT ON (symbol) symbol, open_price AS day_open
            FROM delta_minute_candle
            WHERE EXTRACT(HOUR FROM minute_ts AT TIME ZONE 'Asia/Kolkata') = 9
              AND EXTRACT(MINUTE FROM minute_ts AT TIME ZONE 'Asia/Kolkata') = 15
            ORDER BY symbol, trade_date DESC
        )
        SELECT l.symbol,
            EXTRACT(HOUR FROM l.minute_ts AT TIME ZONE 'Asia/Kolkata')::int AS ist_hour,
            EXTRACT(MINUTE FROM l.minute_ts AT TIME ZONE 'Asia/Kolkata')::int AS ist_minute,
            EXTRACT(DOW FROM l.trade_date)::int AS day_of_week,
            CASE WHEN do_.day_open > 0 THEN (l.close_price - do_.day_open) / do_.day_open * 100 ELSE 0 END AS price_vs_open,
            CASE WHEN l.open_price > 0 THEN (l.close_price - l.open_price) / l.open_price * 100 ELSE 0 END AS candle_body_pct,
            CASE WHEN l.high_price > l.low_price THEN
                (l.high_price - GREATEST(l.open_price, l.close_price)) / (l.high_price - l.low_price) ELSE 0 END AS upper_wick_ratio,
            CASE WHEN l.high_price > l.low_price THEN
                (LEAST(l.open_price, l.close_price) - l.low_price) / (l.high_price - l.low_price) ELSE 0 END AS lower_wick_ratio,
            l.vol_ratio,
            COALESCE(pm.prev_1m_move, 0) AS prev_1m_move
        FROM latest l
        LEFT JOIN prev_min pm ON pm.symbol = l.symbol
        LEFT JOIN day_open do_ ON do_.symbol = l.symbol
        """
        df = pd.read_sql(sql, conn)
        log.info("Scoring %d symbols with ML model", len(df))

        if len(df) == 0:
            log.warning("No recent data to score")
            return

        X = df[FEATURE_COLS].fillna(0).clip(-10, 10)
        probs = model.predict_proba(X)
        up_prob   = probs[:, 1]
        down_prob = 1 - up_prob
        confidence = np.abs(up_prob - 0.5)

        rows = [
            (str(df.iloc[i]['symbol']),
             float(round(up_prob[i], 4)),
             float(round(down_prob[i], 4)),
             float(round(confidence[i], 4)),
             json.dumps(importance))
            for i in range(len(df))
        ]

        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO delta_ml_predictions
                   (symbol, ml_up_prob, ml_down_prob, ml_confidence, feature_importance)
                   VALUES %s
                   ON CONFLICT (symbol) DO UPDATE SET
                     ml_up_prob = EXCLUDED.ml_up_prob,
                     ml_down_prob = EXCLUDED.ml_down_prob,
                     ml_confidence = EXCLUDED.ml_confidence,
                     feature_importance = EXCLUDED.feature_importance,
                     updated_at = NOW()""",
                rows, page_size=200
            )
        conn.commit()
        log.info("Saved ML predictions for %d symbols", len(rows))
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", default="all", choices=["2", "3", "all"])
    parser.add_argument("--days",  type=int, default=60)
    args = parser.parse_args()

    start = datetime.now()
    if args.level in ("2", "all"):
        build_level2(args.days)
    if args.level in ("3", "all"):
        build_level3(args.days)

    log.info("Done in %.1fs", (datetime.now() - start).total_seconds())


if __name__ == "__main__":
    main()
