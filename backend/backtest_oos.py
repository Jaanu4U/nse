"""
OUT-OF-SAMPLE (walk-forward) backtest for the Top-Picks ranking.

The production saved models are trained on ALL history, so scoring them on past dates is
in-sample recall (leaks the future -> fake ~94% win rate). This harness instead, for each
stock, *retrains* on data up to a cutoff and evaluates ONLY on the held-out forward window
the model never saw — an honest estimate of real next-day accuracy.

Run inside the backend container:
    docker compose exec -T backend python backtest_oos.py [HOLDOUT_DAYS] [TOP_N] [SAMPLE]
"""
import sys
import datetime
import statistics
import random
import numpy as np
import xgboost as xgb

from app.database import SessionLocal
from app.services.prediction import PredictionEngine
from app.services.screener import (
    pick_final_score, MIN_PICK_PRICE, MIN_PICK_TURNOVER, PICK_LIQUIDITY_WINDOW_DAYS,
)
from app.models.models import PriceDaily
from sqlalchemy import func, text

HOLDOUT = int(sys.argv[1]) if len(sys.argv) > 1 else 30
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 25
SAMPLE = int(sys.argv[3]) if len(sys.argv) > 3 else 400

THRESHOLDS = [0.01, 0.02, 0.03, 0.05]
WEIGHTS = {0.01: 0.40, 0.02: 0.30, 0.03: 0.20, 0.05: 0.10}


def liquid_symbols(db):
    latest = db.query(func.max(PriceDaily.timestamp)).scalar()
    cutoff = latest - datetime.timedelta(days=PICK_LIQUIDITY_WINDOW_DAYS)
    rows = db.execute(text("""
        WITH turnover AS (
            SELECT stock_id, AVG(close*volume) AS avg_turnover
            FROM prices_daily WHERE timestamp >= :cutoff GROUP BY stock_id
        ), lastclose AS (
            SELECT DISTINCT ON (stock_id) stock_id, close
            FROM prices_daily ORDER BY stock_id, timestamp DESC
        )
        SELECT s.symbol FROM stocks s
        JOIN turnover t ON t.stock_id = s.id
        JOIN lastclose lc ON lc.stock_id = s.id
        WHERE s.is_active = TRUE AND lc.close >= :minprice AND t.avg_turnover >= :minturn
    """), {"cutoff": cutoff, "minprice": MIN_PICK_PRICE, "minturn": MIN_PICK_TURNOVER})
    return [r[0] for r in rows]


def train_oos(X_train, y_train_raw):
    """Train the same regularised models used in production, on the truncated window."""
    models = {}
    for th in THRESHOLDS:
        y_bin = (y_train_raw >= th).astype(int)
        n_pos = int(y_bin.sum())
        if n_pos == 0:
            models[th] = 0.0
            continue
        if n_pos == len(y_bin):
            models[th] = 1.0
            continue
        m = xgb.XGBClassifier(
            n_estimators=120, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
            reg_lambda=2.0, random_state=42, eval_metric="logloss",
        )
        m.fit(X_train, y_bin)
        models[th] = m
    return models


def composite(models, X):
    comp = np.zeros(len(X))
    for th, w in WEIGHTS.items():
        m = models.get(th)
        if m is None:
            continue
        comp += w * (m if isinstance(m, float) else m.predict_proba(X)[:, 1])
    return comp


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)
    symbols = liquid_symbols(db)
    random.seed(42)
    if len(symbols) > SAMPLE:
        symbols = random.sample(symbols, SAMPLE)
    print(f"OOS walk-forward | universe {len(symbols)} stocks | holdout {HOLDOUT}d | top {TOP_N}")

    by_date = {}   # date -> [(sym, final_score, ml, next_ret)]
    processed = 0
    for sym in symbols:
        X, y = engine._prepare_data(sym)
        if X is None or len(X) < 150:
            continue
        nd_all = y["next_day_return"].values
        n = len(X)
        split = n - HOLDOUT - 1          # last valid training row (has a realised target)
        if split < 120:
            continue

        X_train = X.iloc[:split]
        y_train = y.iloc[:split]["next_day_return"]
        # Drop rows whose target is NaN (shouldn't be, but guard)
        mask = ~y_train.isna()
        if mask.sum() < 100:
            continue
        try:
            models = train_oos(X_train[mask], y_train[mask])
        except Exception:
            continue

        X_test = X.iloc[split:n]          # rows the model never trained on
        comp = composite(models, X_test)
        idx = X_test.index
        d20 = X_test["dist_ema_20"].values
        d50 = X_test["dist_ema_50"].values
        d200 = X_test["dist_ema_200"].values
        st = X_test["supertrend_dir"].values
        adx = X_test["adx"].values
        rsi = X_test["rsi"].values
        atrr = X_test["atr_ratio"].values
        nd = nd_all[split:n]

        for i in range(len(X_test)):
            r = nd[i]
            if r is None or (isinstance(r, float) and np.isnan(r)):
                continue
            ml = float(comp[i])
            final = pick_final_score(
                ml, 0.0,
                close_gt_ema50=bool(d50[i] > 0),
                ema20_gt_ema50=bool(d20[i] < d50[i]),
                close_gt_ema200=bool(d200[i] > 0),
                supertrend_dir=int(st[i]) if not np.isnan(st[i]) else None,
                adx=float(adx[i]) if not np.isnan(adx[i]) else None,
                rsi=float(rsi[i]) if not np.isnan(rsi[i]) else None,
                atr_ratio=float(atrr[i]) if not np.isnan(atrr[i]) else None,
            )
            by_date.setdefault(idx[i], []).append((sym, final, ml, float(r)))
        processed += 1
        if processed % 50 == 0:
            print(f"  ...trained {processed} stocks")

    print(f"Trained {processed} stocks out-of-sample.\n")
    dates = sorted(by_date.keys())[-HOLDOUT:]

    def evaluate(key):
        dr, dw, t10, mk = [], [], [], []
        for d in dates:
            recs = by_date[d]
            if len(recs) < TOP_N:
                continue
            ranked = sorted(recs, key=lambda t: t[key], reverse=True)
            top = ranked[:TOP_N]
            rets = [t[3] for t in top]
            dr.append(statistics.mean(rets))
            dw.append(sum(1 for x in rets if x > 0) / len(rets))
            t10.append(statistics.mean([t[3] for t in ranked[:10]]))
            mk.append(statistics.mean([t[3] for t in recs]))
        return dr, dw, t10, mk

    for label, key in [("TREND+VOL ADJUSTED (production)", 1), ("RAW ML ONLY (old logic)", 2)]:
        dr, dw, t10, mk = evaluate(key)
        if not dr:
            print(f"{label}: not enough data per day (need >= {TOP_N})")
            continue
        print(f"== {label} ==")
        print(f"  days evaluated      : {len(dr)}")
        print(f"  avg next-day return : {statistics.mean(dr)*100:+.3f}%  (top {TOP_N})")
        print(f"  avg top-10 return   : {statistics.mean(t10)*100:+.3f}%")
        print(f"  win rate            : {statistics.mean(dw)*100:.1f}%")
        print(f"  market baseline     : {statistics.mean(mk)*100:+.3f}%  (all liquid sampled)")
        print(f"  edge vs market      : {(statistics.mean(dr)-statistics.mean(mk))*100:+.3f}%")
        print(f"  % up-days           : {sum(1 for x in dr if x>0)/len(dr)*100:.1f}%")
        print()

    db.close()


if __name__ == "__main__":
    main()
