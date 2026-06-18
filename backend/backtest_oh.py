"""
PROTOTYPE: Open->High targeted Top-Picks backtest (does NOT touch production).

Difference vs backtest_stars.py:
  * TARGET  -> classifiers are trained on next-day (high-open)/open >= threshold,
               i.e. the EXACT "star" metric we grade on, instead of close-to-close.
  * COMPOSITE -> reweighted toward the +2% bucket (the stated main target:
               "next day open->high >= 2%").
  * FEATURES -> on top of the 27 production features, this prototype adds
               intraday-range / momentum-expansion features (lever B) that directly
               describe "is tomorrow likely to print a big open->high move":
                 atr_expansion    today's range vs its own 20-day average
                 close_pos_range  where close sits inside today's high-low
                 oh_today         today's own (high-open)/open (target autocorrelation)
                 oh_mean_20       average open->high move over the last 20 sessions
                 oh_freq2_20      fraction of last 20 sessions with open->high >= 2%
                 dist_prior_high5 close vs the highest high of the prior 5 sessions (ORB proxy)
               All use data up to and including day t to predict day t+1 (no leakage).

Everything else (the 27 base features, trend/vol/RSI re-ranking, hard exclusions, OOS
walk-forward training at the window boundary) matches backtest_stars.py so the comparison
is apples-to-apples.

Run inside the backend container:
    docker compose exec -T backend python backtest_oh.py [K_DAYS] [TOP_N] [MODEL]
    MODEL = xgb (default) | lgbm | ensemble
"""
import sys
import datetime
import numpy as np
import pandas as pd

from app.database import SessionLocal
from app.services.prediction import PredictionEngine
from app.services.screener import (
    pick_final_score, MIN_PICK_PRICE, MIN_PICK_TURNOVER, PICK_LIQUIDITY_WINDOW_DAYS,
    PICK_MAX_RSI, PICK_EXCLUDE_SUPERTREND_DOWN,
)
from app.models.models import PriceDaily, Stock
from sqlalchemy import func, text

K_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 15
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 25
MODEL = sys.argv[3] if len(sys.argv) > 3 else "xgb"

# Open->High thresholds. Main target = +2%, so the composite leans on P(+2%).
OH_THRESHOLDS = [0.01, 0.02, 0.03, 0.05]
COMPOSITE_WEIGHTS = {0.01: 0.15, 0.02: 0.40, 0.03: 0.30, 0.05: 0.15}
MIN_TRAIN_ROWS = 100


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
        SELECT s.symbol
        FROM stocks s
        JOIN turnover t ON t.stock_id = s.id
        JOIN lastclose lc ON lc.stock_id = s.id
        WHERE s.is_active = TRUE AND lc.close >= :minprice AND t.avg_turnover >= :minturn
    """), {"cutoff": cutoff, "minprice": MIN_PICK_PRICE, "minturn": MIN_PICK_TURNOVER})
    return [r[0] for r in rows]


def next_day_oh(db, stock_id, index):
    """next_open, next_high and the open->high target, aligned to feature index."""
    rows = db.query(PriceDaily.timestamp, PriceDaily.open, PriceDaily.high)\
        .filter(PriceDaily.stock_id == stock_id).order_by(PriceDaily.timestamp).all()
    if not rows:
        return None
    df = pd.DataFrame([{"date": r[0], "open": float(r[1]), "high": float(r[2])} for r in rows]).set_index("date")
    df["next_open"] = df["open"].shift(-1)
    df["next_high"] = df["high"].shift(-1)
    df["oh"] = (df["next_high"] - df["next_open"]) / df["next_open"]
    return df[["next_open", "next_high", "oh"]].reindex(index)


# Lever-B feature columns this prototype appends to the production feature matrix.
EXTRA_FEATURES = [
    "atr_expansion", "close_pos_range", "oh_today",
    "oh_mean_20", "oh_freq2_20", "dist_prior_high5",
]


def extra_features(db, stock_id, index):
    """Intraday-range / momentum-expansion features (lever B), aligned to feature index.

    Every column uses only OHLC up to and including day t, so it is a valid predictor
    of day t+1's open->high move (no look-ahead).
    """
    rows = db.query(PriceDaily.timestamp, PriceDaily.open, PriceDaily.high,
                    PriceDaily.low, PriceDaily.close)\
        .filter(PriceDaily.stock_id == stock_id).order_by(PriceDaily.timestamp).all()
    if not rows:
        return None
    df = pd.DataFrame([{
        "date": r[0], "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]),
    } for r in rows]).set_index("date")

    rng = (df["high"] - df["low"]).clip(lower=0)
    rng_pct = rng / df["close"]
    df["atr_expansion"] = rng_pct / rng_pct.rolling(20, min_periods=5).mean()
    df["close_pos_range"] = ((df["close"] - df["low"]) / rng.replace(0, np.nan)).clip(0, 1)

    oh = (df["high"] - df["open"]) / df["open"]
    df["oh_today"] = oh
    df["oh_mean_20"] = oh.rolling(20, min_periods=5).mean()
    df["oh_freq2_20"] = (oh >= 0.02).rolling(20, min_periods=5).mean()

    prior_high5 = df["high"].shift(1).rolling(5, min_periods=2).max()
    df["dist_prior_high5"] = (df["close"] - prior_high5) / prior_high5

    out = df[EXTRA_FEATURES].reindex(index)
    out = out.fillna({
        "atr_expansion": 1.0, "close_pos_range": 0.5, "oh_today": 0.0,
        "oh_mean_20": 0.0, "oh_freq2_20": 0.0, "dist_prior_high5": 0.0,
    })
    return out


def train_window(engine, X_train, oh_train, model_type):
    """Train one classifier per OH threshold -> {th: est | list | float}."""
    models = {}
    for th in OH_THRESHOLDS:
        y_bin = (oh_train >= th).astype(int)
        n_pos = int(y_bin.sum())
        if n_pos == 0:
            models[th] = 0.0
            continue
        if n_pos == len(y_bin):
            models[th] = 1.0
            continue
        if model_type == "ensemble":
            ests = []
            for kind in ("xgb", "lgbm"):
                est = engine._new_estimator(kind)
                est.fit(X_train, y_bin)
                ests.append(est)
            models[th] = ests
        else:
            est = engine._new_estimator(model_type)
            est.fit(X_train, y_bin)
            models[th] = est
    return models


def composite_for(models, Xe):
    comp = np.zeros(len(Xe))
    for th, w in COMPOSITE_WEIGHTS.items():
        m = models.get(th)
        if m is None:
            continue
        if isinstance(m, float):
            comp += w * m
        elif isinstance(m, list):
            comp += w * np.mean([e.predict_proba(Xe)[:, 1] for e in m], axis=0)
        else:
            comp += w * m.predict_proba(Xe)[:, 1]
    return comp


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)
    symbols = liquid_symbols(db)
    sym_to_id = {s.symbol: s.id for s in
                 db.query(Stock.symbol, Stock.id).filter(Stock.symbol.in_(symbols)).all()}
    print(f"[PROTOTYPE: open->high target + lever-B features] universe={len(symbols)} | "
          f"OOS last {K_DAYS} sessions | top {TOP_N} | model={MODEL}", flush=True)
    print(f"composite weights {COMPOSITE_WEIGHTS}", flush=True)
    print(f"extra features  {EXTRA_FEATURES}\n", flush=True)

    by_date = {}
    processed = skipped = 0
    for n_done, sym in enumerate(symbols, 1):
        if n_done % 100 == 0:
            print(f"  ...trained {n_done}/{len(symbols)} (used {processed})", flush=True)
        X, _y = engine._prepare_data(sym)
        if X is None:
            skipped += 1
            continue
        ex = extra_features(db, sym_to_id[sym], X.index)
        if ex is not None:
            X = X.join(ex)
        n = len(X)
        test_start = n - 1 - K_DAYS
        if test_start < MIN_TRAIN_ROWS:
            skipped += 1
            continue

        nd = next_day_oh(db, sym_to_id[sym], X.index)
        if nd is None:
            skipped += 1
            continue

        # OOS: train on rows before the window whose OH target is known.
        oh_all = nd["oh"]
        train_idx = X.index[:test_start]
        oh_train = oh_all.loc[train_idx].dropna()
        X_train = X.loc[oh_train.index]
        if len(X_train) < MIN_TRAIN_ROWS:
            skipped += 1
            continue

        try:
            models = train_window(engine, X_train, oh_train, MODEL)
        except Exception:
            skipped += 1
            continue

        test = X.iloc[test_start:n - 1]
        comp = composite_for(models, test)
        no = nd["next_open"].reindex(test.index).values
        nh = nd["next_high"].reindex(test.index).values
        rsi = test["rsi"].values
        adx = test["adx"].values
        d20 = test["dist_ema_20"].values
        d50 = test["dist_ema_50"].values
        d200 = test["dist_ema_200"].values
        st = test["supertrend_dir"].values
        atrr = test["atr_ratio"].values
        idx = test.index

        for i in range(len(test)):
            o, h = no[i], nh[i]
            if o is None or h is None or np.isnan(o) or np.isnan(h) or o <= 0:
                continue
            rsi_i = float(rsi[i]) if not np.isnan(rsi[i]) else None
            st_i = int(st[i]) if not np.isnan(st[i]) else None
            if PICK_EXCLUDE_SUPERTREND_DOWN and st_i == -1:
                continue
            if rsi_i is not None and rsi_i >= PICK_MAX_RSI:
                continue
            ml = float(comp[i])
            final = pick_final_score(
                ml, 0.0,
                close_gt_ema50=bool(d50[i] > 0),
                ema20_gt_ema50=bool(d20[i] < d50[i]),
                close_gt_ema200=bool(d200[i] > 0),
                supertrend_dir=st_i,
                adx=float(adx[i]) if not np.isnan(adx[i]) else None,
                rsi=rsi_i,
                atr_ratio=float(atrr[i]) if not np.isnan(atrr[i]) else None,
                dist_ema20=float(d20[i]) if not np.isnan(d20[i]) else None,
            )
            by_date.setdefault(idx[i], []).append((sym, final, ml, (h - o) / o))
        processed += 1

    print(f"\nProcessed {processed} stocks ({skipped} skipped).\n", flush=True)
    dates = sorted(by_date.keys())[-K_DAYS:]

    def stars_of(pct):
        return min(5, int(np.floor(pct * 100))) if pct > 0 else 0

    def report(top_k, label):
        tot = g1 = g2 = g3 = neg = 0
        pcts_all = []
        for d in dates:
            recs = by_date[d]
            if len(recs) < top_k:
                continue
            ranked = sorted(recs, key=lambda t: t[1], reverse=True)[:top_k]
            pcts = [t[3] for t in ranked]
            tot += len(pcts)
            g1 += sum(1 for p in pcts if stars_of(p) >= 1)
            g2 += sum(1 for p in pcts if stars_of(p) >= 2)
            g3 += sum(1 for p in pcts if stars_of(p) >= 3)
            neg += sum(1 for p in pcts if p <= 0)
            pcts_all.extend(pcts)
        if not tot:
            return
        print(f"== {label} ({tot} picks over {tot//top_k} days, model={MODEL}) ==")
        print(f"  >=1 star (O->H >=1%): {g1:>4}  ({g1/tot*100:.1f}%)")
        print(f"  >=2 star (O->H >=2%): {g2:>4}  ({g2/tot*100:.1f}%)   <- MAIN TARGET")
        print(f"  >=3 star (O->H >=3%): {g3:>4}  ({g3/tot*100:.1f}%)")
        print(f"  avg open->high gain : {np.mean(pcts_all)*100:+.2f}%")
        print(f"  median open->high   : {np.median(pcts_all)*100:+.2f}%")
        print(f"  picks with NO upside: {neg}  ({neg/tot*100:.1f}%)\n")

    report(TOP_N, f"TOP {TOP_N}")
    report(5, "TOP 5")

    # Top-5 names per day for inspection.
    print("===== TOP 5 PICKS PER DAY (by name) =====")
    for d in dates:
        recs = by_date[d]
        if len(recs) < 5:
            continue
        top5 = sorted(recs, key=lambda t: t[1], reverse=True)[:5]
        print(f"\n{d}:")
        print(f"    {'#':<2}{'symbol':<14}{'conf':>7}{'O->H':>9}{'stars':>8}")
        for rnk, (sym, final, ml, pct) in enumerate(top5, 1):
            st = stars_of(pct)
            print(f"    {rnk:<2}{sym:<14}{final*100:>6.1f}{pct*100:>8.2f}%{('*'*st) if st else '-':>8}")

    db.close()


if __name__ == "__main__":
    main()
