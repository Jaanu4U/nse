"""
Out-of-sample 15-day Open->High "star" backtest for the Top-Picks ranking.

For every liquid stock we:
  1. build the full feature matrix from the DB price/indicator history,
  2. TRAIN the model on all data BEFORE the test window (out-of-sample / walk-forward
     at the window boundary -- the model never sees the tested days),
  3. PREDICT P(+1/+2/+3/+5%) for each of the last K sessions,
  4. rank the production composite + trend/vol/RSI score with the SAME hard exclusions
     as screener.get_top_picks, take the top-N each day,
  5. measure the realised NEXT-day Open->High gain and award stars
     ( stars = floor((high-open)/open * 100), capped 5 ), the exact scorecard metric.

Because the model is retrained at the window boundary, the results are genuinely
out-of-sample (unlike loading the in-sample saved models). Each stock is trained once.

Run inside the backend container (long job, runs the whole liquid universe):
    docker compose exec -T backend python backtest_stars.py [K_DAYS] [TOP_N] [MODEL]
    MODEL = xgb (default, fast) | lgbm | ensemble (xgb+lgbm, ~2x slower, matches prod)
"""
import sys
import datetime
import numpy as np
import pandas as pd

from app.database import SessionLocal
from app.services.prediction import PredictionEngine, THRESHOLDS
from app.services.screener import (
    pick_final_score, MIN_PICK_PRICE, MIN_PICK_TURNOVER, PICK_LIQUIDITY_WINDOW_DAYS,
    PICK_MAX_RSI, PICK_EXCLUDE_SUPERTREND_DOWN,
)
from app.models.models import PriceDaily, Stock
from sqlalchemy import func, text

K_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 15
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 25
MODEL = sys.argv[3] if len(sys.argv) > 3 else "xgb"

COMPOSITE_WEIGHTS = {0.01: 0.40, 0.02: 0.30, 0.03: 0.20, 0.05: 0.10}
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


def train_window(engine, X_train, y_train_raw, model_type):
    """Replicate PredictionEngine.train_models on an explicit slice -> {th: est|list|float}."""
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


def next_day_ohlc(db, stock_id, index):
    rows = db.query(PriceDaily.timestamp, PriceDaily.open, PriceDaily.high)\
        .filter(PriceDaily.stock_id == stock_id).order_by(PriceDaily.timestamp).all()
    if not rows:
        return None
    df = pd.DataFrame([{"date": r[0], "open": float(r[1]), "high": float(r[2])} for r in rows]).set_index("date")
    df["next_open"] = df["open"].shift(-1)
    df["next_high"] = df["high"].shift(-1)
    return df[["next_open", "next_high"]].reindex(index)


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)
    symbols = liquid_symbols(db)
    sym_to_id = {s.symbol: s.id for s in
                 db.query(Stock.symbol, Stock.id).filter(Stock.symbol.in_(symbols)).all()}
    print(f"Liquid universe: {len(symbols)} stocks | OOS last {K_DAYS} sessions | "
          f"top {TOP_N} | model={MODEL}\n", flush=True)

    by_date = {}   # date -> [(sym, final_score, ml, star_pct)]
    processed = skipped = 0
    for n_done, sym in enumerate(symbols, 1):
        if n_done % 100 == 0:
            print(f"  ...trained {n_done}/{len(symbols)} (used {processed})", flush=True)
        X, y = engine._prepare_data(sym)
        if X is None:
            skipped += 1
            continue
        n = len(X)
        test_start = n - 1 - K_DAYS          # first tested feature-row position
        if test_start < MIN_TRAIN_ROWS:
            skipped += 1
            continue

        # Train strictly on rows before the test window (their targets resolve before it).
        X_train = X.iloc[:test_start]
        y_train_raw = y.iloc[:test_start]["next_day_return"].dropna()
        X_train = X_train.loc[y_train_raw.index]
        if len(X_train) < MIN_TRAIN_ROWS:
            skipped += 1
            continue

        try:
            models = train_window(engine, X_train, y_train_raw, MODEL)
        except Exception:
            skipped += 1
            continue

        nd = next_day_ohlc(db, sym_to_id[sym], X.index)
        if nd is None:
            skipped += 1
            continue

        test = X.iloc[test_start:n - 1]      # each has a real next session
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

    tot = ge1 = ge2 = ge3 = neg = 0
    all_pct = []
    print(f"{'date':<12}{'picks':>6}{'>=1*':>7}{'>=2*':>7}{'>=3*':>7}{'avgO>H':>9}{'bestO>H':>9}")
    print("-" * 57)
    for d in dates:
        recs = by_date[d]
        if len(recs) < TOP_N:
            continue
        ranked = sorted(recs, key=lambda t: t[1], reverse=True)[:TOP_N]
        pcts = [t[3] for t in ranked]
        g1 = sum(1 for p in pcts if stars_of(p) >= 1)
        g2 = sum(1 for p in pcts if stars_of(p) >= 2)
        g3 = sum(1 for p in pcts if stars_of(p) >= 3)
        tot += len(pcts); ge1 += g1; ge2 += g2; ge3 += g3
        neg += sum(1 for p in pcts if p <= 0)
        all_pct.extend(pcts)
        print(f"{str(d):<12}{len(pcts):>6}{g1:>7}{g2:>7}{g3:>7}{np.mean(pcts)*100:>8.2f}%{max(pcts)*100:>8.2f}%")

    if tot:
        print("-" * 57)
        print(f"\nAGGREGATE | {tot} picks over {tot//TOP_N} days | model={MODEL} (out-of-sample):")
        print(f"  >=1 star (O->H >=1%): {ge1:>4}  ({ge1/tot*100:.1f}%)")
        print(f"  >=2 star (O->H >=2%): {ge2:>4}  ({ge2/tot*100:.1f}%)")
        print(f"  >=3 star (O->H >=3%): {ge3:>4}  ({ge3/tot*100:.1f}%)")
        print(f"  avg open->high gain : {np.mean(all_pct)*100:+.2f}%")
        print(f"  median open->high   : {np.median(all_pct)*100:+.2f}%")
        print(f"  picks with NO upside: {neg}  ({neg/tot*100:.1f}%)")
    else:
        print("Not enough per-day candidates to fill top-N.")

    # ---- TOP 5 PICKS PER DAY, BY NAME (clear data) ----
    print("\n\n===== TOP 5 PICKS PER DAY (by name) =====")
    t5_tot = t5_ge1 = t5_ge2 = t5_ge3 = 0
    t5_pct = []
    for d in dates:
        recs = by_date[d]
        if len(recs) < 5:
            continue
        top5 = sorted(recs, key=lambda t: t[1], reverse=True)[:5]
        print(f"\n{d}:")
        print(f"    {'#':<2}{'symbol':<14}{'conf':>7}{'O->H':>9}{'stars':>8}")
        for rnk, (sym, final, ml, pct) in enumerate(top5, 1):
            st = stars_of(pct)
            star_str = ("*" * st) if st else "-"
            print(f"    {rnk:<2}{sym:<14}{final*100:>6.1f}{pct*100:>8.2f}%{star_str:>8}")
            t5_tot += 1
            t5_pct.append(pct)
            if st >= 1: t5_ge1 += 1
            if st >= 2: t5_ge2 += 1
            if st >= 3: t5_ge3 += 1
    if t5_tot:
        print(f"\nTOP-5 AGGREGATE | {t5_tot} picks over {t5_tot//5} days | model={MODEL}:")
        print(f"  >=1 star: {t5_ge1:>3}  ({t5_ge1/t5_tot*100:.1f}%)")
        print(f"  >=2 star: {t5_ge2:>3}  ({t5_ge2/t5_tot*100:.1f}%)")
        print(f"  >=3 star: {t5_ge3:>3}  ({t5_ge3/t5_tot*100:.1f}%)")
        print(f"  avg open->high : {np.mean(t5_pct)*100:+.2f}%")
        print(f"  median open->high : {np.median(t5_pct)*100:+.2f}%")
    db.close()


if __name__ == "__main__":
    main()
