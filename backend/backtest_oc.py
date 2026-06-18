"""
HONEST tradeable backtest: realized OPEN->CLOSE PnL (buy next open, sell next close).

Unlike backtest_oh.py (which trained on open->high and reported the un-tradeable
open->high "star" metric), this harness ALWAYS measures the realized return of an
actionable strategy:

    on day t, rank stocks using features known at t's close;
    buy the top-N at day t+1's OPEN, sell them at day t+1's CLOSE;
    realized return per pick = (next_close - next_open) / next_open.

The training TARGET is selectable so the same harness compares, apples-to-apples,
which target produces the best *realized* open->close PnL:

    cc  close-to-close  P((next_close - close)/close >= th)          (old prod model)
    oh  open-to-high    P((next_high - next_open)/next_open >= th)   (current prod model)
    oc  open-to-close   P((next_close - next_open)/next_open >= th)  (proposed tradeable)

Everything else (production 33-feature matrix from _prepare_data, trend/RSI re-ranking,
hard exclusions, OOS walk-forward training at the window boundary) is identical across
targets, so the only thing that changes is what the classifiers learn to rank by.

A universe baseline (mean open->close of every liquid name each day) is printed so we
can see whether the picks beat simply buying the whole liquid universe.

Run inside the backend container:
    docker compose exec -T backend python backtest_oc.py [K_DAYS] [TOP_N] [TARGET] [MODEL]
    TARGET = oc (default) | oh | cc
    MODEL  = xgb (default) | lgbm | ensemble
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
from app.utils.job_progress import start_job, set_progress, finish_job, fail_job

K_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 25
TARGET = sys.argv[3] if len(sys.argv) > 3 else "oc"
MODEL = sys.argv[4] if len(sys.argv) > 4 else "xgb"

# Per-target thresholds + composite weights. open->close moves are smaller than
# open->high, so the oc target leans on "will it close green" plus small magnitudes.
TARGET_CFG = {
    "cc": {"th": [0.01, 0.02, 0.03, 0.05], "w": {0.01: 0.15, 0.02: 0.40, 0.03: 0.30, 0.05: 0.15}},
    "oh": {"th": [0.01, 0.02, 0.03, 0.05], "w": {0.01: 0.15, 0.02: 0.40, 0.03: 0.30, 0.05: 0.15}},
    "oc": {"th": [0.0, 0.005, 0.01, 0.02], "w": {0.0: 0.30, 0.005: 0.30, 0.01: 0.25, 0.02: 0.15}},
}
CFG = TARGET_CFG[TARGET]
THRESHOLDS = CFG["th"]
COMPOSITE_WEIGHTS = CFG["w"]
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


def targets(db, stock_id, index):
    """next_open, next_close, next_high and the cc/oh/oc targets, aligned to feature index."""
    rows = db.query(PriceDaily.timestamp, PriceDaily.open, PriceDaily.high,
                    PriceDaily.close)\
        .filter(PriceDaily.stock_id == stock_id).order_by(PriceDaily.timestamp).all()
    if not rows:
        return None
    df = pd.DataFrame([{
        "date": r[0], "open": float(r[1]), "high": float(r[2]), "close": float(r[3]),
    } for r in rows]).set_index("date")
    df["next_open"] = df["open"].shift(-1)
    df["next_high"] = df["high"].shift(-1)
    df["next_close"] = df["close"].shift(-1)
    df["cc"] = (df["next_close"] - df["close"]) / df["close"]
    df["oh"] = (df["next_high"] - df["next_open"]) / df["next_open"]
    df["oc"] = (df["next_close"] - df["next_open"]) / df["next_open"]
    return df[["next_open", "next_close", "next_high", "cc", "oh", "oc"]].reindex(index)


def train_window(engine, X_train, y_train, model_type):
    """Train one classifier per threshold on the chosen target -> {th: est | list | float}."""
    models = {}
    for th in THRESHOLDS:
        y_bin = (y_train >= th).astype(int)
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
    test_name = f"Backtest (open->close, target={TARGET}, model={MODEL}, {K_DAYS}d OOS, top{TOP_N})"
    start_job("backtest", total=len(symbols),
              message=f"{test_name} — training walk-forward models on {len(symbols)} liquid stocks")
    print(f"[TRADEABLE open->close backtest] target={TARGET} | universe={len(symbols)} | "
          f"OOS last {K_DAYS} sessions | top {TOP_N} | model={MODEL}", flush=True)
    print(f"thresholds {THRESHOLDS}  composite {COMPOSITE_WEIGHTS}\n", flush=True)

    by_date = {}          # date -> list of (sym, final_score, ml, realized_oc)
    universe_oc = {}      # date -> list of realized_oc for every evaluable liquid name
    processed = skipped = 0
    for n_done, sym in enumerate(symbols, 1):
        if n_done % 25 == 0:
            set_progress(current=n_done,
                         message=f"{test_name} — trained {n_done}/{len(symbols)} (used {processed})")
        if n_done % 100 == 0:
            print(f"  ...trained {n_done}/{len(symbols)} (used {processed})", flush=True)
        X, _y = engine._prepare_data(sym)
        if X is None:
            skipped += 1
            continue
        n = len(X)
        test_start = n - 1 - K_DAYS
        if test_start < MIN_TRAIN_ROWS:
            skipped += 1
            continue

        tg = targets(db, sym_to_id[sym], X.index)
        if tg is None:
            skipped += 1
            continue

        y_all = tg[TARGET]
        train_idx = X.index[:test_start]
        y_train = y_all.loc[train_idx].dropna()
        X_train = X.loc[y_train.index]
        if len(X_train) < MIN_TRAIN_ROWS:
            skipped += 1
            continue

        try:
            models = train_window(engine, X_train, y_train, MODEL)
        except Exception:
            skipped += 1
            continue

        test = X.iloc[test_start:n - 1]
        comp = composite_for(models, test)
        no = tg["next_open"].reindex(test.index).values
        nc = tg["next_close"].reindex(test.index).values
        rsi = test["rsi"].values
        adx = test["adx"].values
        d20 = test["dist_ema_20"].values
        d50 = test["dist_ema_50"].values
        d200 = test["dist_ema_200"].values
        st = test["supertrend_dir"].values
        atrr = test["atr_ratio"].values
        idx = test.index

        for i in range(len(test)):
            o, c = no[i], nc[i]
            if o is None or c is None or np.isnan(o) or np.isnan(c) or o <= 0:
                continue
            realized = (c - o) / o
            # universe baseline: every evaluable liquid name (before exclusions)
            universe_oc.setdefault(idx[i], []).append(realized)

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
            by_date.setdefault(idx[i], []).append((sym, final, ml, realized))
        processed += 1

    print(f"\nProcessed {processed} stocks ({skipped} skipped).\n", flush=True)
    set_progress(current=len(symbols),
                 message=f"{test_name} — computing realized PnL over last {K_DAYS} sessions")
    dates = sorted(by_date.keys())[-K_DAYS:]

    def report(top_k, label):
        tot = pos = h1 = h2 = 0
        pcts_all = []
        daily_means = []      # equal-weight portfolio daily return
        bench_means = []      # universe baseline daily return (same days)
        for d in dates:
            recs = by_date[d]
            if len(recs) < top_k:
                continue
            ranked = sorted(recs, key=lambda t: t[1], reverse=True)[:top_k]
            pcts = [t[3] for t in ranked]
            tot += len(pcts)
            pos += sum(1 for p in pcts if p > 0)
            h1 += sum(1 for p in pcts if p >= 0.01)
            h2 += sum(1 for p in pcts if p >= 0.02)
            pcts_all.extend(pcts)
            daily_means.append(np.mean(pcts))
            bench_means.append(np.mean(universe_oc.get(d, [0.0])))
        if not tot:
            print(f"== {label}: no days with >= {top_k} picks ==\n")
            return
        ndays = len(daily_means)
        port_cum = np.prod([1 + m for m in daily_means]) - 1
        bench_cum = np.prod([1 + m for m in bench_means]) - 1
        print(f"== {label} ({tot} picks over {ndays} days, target={TARGET}, model={MODEL}) ==")
        print(f"  avg realized open->close : {np.mean(pcts_all)*100:+.3f}%   <- TRADEABLE PnL/pick")
        print(f"  median realized          : {np.median(pcts_all)*100:+.3f}%")
        print(f"  picks closing GREEN      : {pos:>4}  ({pos/tot*100:.1f}%)")
        print(f"  hit >= +1%               : {h1:>4}  ({h1/tot*100:.1f}%)")
        print(f"  hit >= +2%               : {h2:>4}  ({h2/tot*100:.1f}%)")
        print(f"  portfolio daily mean     : {np.mean(daily_means)*100:+.3f}%/day")
        print(f"  portfolio cumulative     : {port_cum*100:+.2f}%  over {ndays} days")
        print(f"  universe baseline cumul. : {bench_cum*100:+.2f}%  (buy whole liquid universe)")
        print(f"  edge vs universe         : {(np.mean(daily_means)-np.mean(bench_means))*100:+.3f}%/day\n")

    report(TOP_N, f"TOP {TOP_N}")
    report(5, "TOP 5")

    print("===== TOP 5 PICKS PER DAY (realized open->close) =====")
    for d in dates:
        recs = by_date[d]
        if len(recs) < 5:
            continue
        top5 = sorted(recs, key=lambda t: t[1], reverse=True)[:5]
        print(f"\n{d}:")
        print(f"    {'#':<2}{'symbol':<14}{'conf':>7}{'O->C':>9}")
        for rnk, (sym, final, ml, pct) in enumerate(top5, 1):
            print(f"    {rnk:<2}{sym:<14}{final*100:>6.1f}{pct*100:>8.2f}%")

    finish_job(message=f"{test_name} — complete ({processed} stocks, {len(dates)} sessions)")
    db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        fail_job(message=f"Backtest failed: {str(e)[:120]}")
        raise

