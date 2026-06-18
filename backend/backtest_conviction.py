"""
CONVICTION TOP-5: a high-conviction, small-basket selector for next-day >= +2% moves.

The user wants 5 picks, not 25, with the strongest possible analysis. A 5-name basket
lives or dies on conviction, so this ranks not by raw ML probability alone but by a
multi-factor CONVICTION score that layers several independent confirmations on top of the
ensemble probability:

  1. ENSEMBLE PROBABILITY   mean P(next-day close-to-close >= +2%/+3%) from xgb + lgbm,
                            leaned toward the +2% bucket (the user's target).
  2. MODEL AGREEMENT        xgb and lgbm must roughly agree; wide disagreement = high model
                            variance = low conviction, so it is penalised.
  3. TREND REGIME           clean uptrend (close>EMA200, EMA20>EMA50, supertrend up, ADX>=18)
                            -> moves follow through instead of fading.
  4. MOMENTUM + VOLUME       recent positive drift with above-average volume = real demand.
  5. OVEREXTENSION GUARD     fade conviction when RSI is hot or price is stretched far above
                            EMA20 (mean-reversion risk).

It compares three selectors on the SAME days / universe so we can see whether the extra
machinery actually helps the top 5:
    A) PLAIN     rank by ensemble composite only
    B) PROD      rank by production pick_final_score
    C) CONVICTION rank by the multi-factor conviction score

For each it reports the realized next-day outcomes that matter for a "+2% next day" goal:
    open->close avg            (buy next open, sell next close — the actionable hold)
    % close-to-close >= +2%    (the model's literal target, pick-day close -> next close)
    % next-high  >= +2%        (a +2% limit order would have filled intraday)
    cumulative + edge vs buying the whole liquid universe.

Run inside the backend container:
    docker compose exec -T backend python backtest_conviction.py [K_DAYS] [MODEL]
    MODEL = ensemble (default) | xgb | lgbm
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
MODEL = sys.argv[2] if len(sys.argv) > 2 else "ensemble"
TOP_N = 5

# Close-to-close thresholds, leaned hard toward the +2% / +3% buckets (the stated target).
THRESHOLDS = [0.01, 0.02, 0.03, 0.05]
COMPOSITE_WEIGHTS = {0.01: 0.10, 0.02: 0.45, 0.03: 0.30, 0.05: 0.15}
MIN_TRAIN_ROWS = 100


def liquid_symbols(db):
    latest = db.query(func.max(PriceDaily.timestamp)).scalar()
    cutoff = latest - datetime.timedelta(days=PICK_LIQUIDITY_WINDOW_DAYS)
    # NSE: no market-cap/shares data, so average daily turnover is the size proxy. Take the
    # TOP 500 most-liquid names (~the Nifty-500 large/mid-cap universe, all >> Rs.1000 Cr mcap).
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
        ORDER BY t.avg_turnover DESC
        LIMIT 500
    """), {"cutoff": cutoff, "minprice": MIN_PICK_PRICE, "minturn": MIN_PICK_TURNOVER})
    return [r[0] for r in rows]


def outcomes(db, stock_id, index):
    """day-t close + next open/high/close, aligned to feature index."""
    rows = db.query(PriceDaily.timestamp, PriceDaily.open, PriceDaily.high,
                    PriceDaily.close)\
        .filter(PriceDaily.stock_id == stock_id).order_by(PriceDaily.timestamp).all()
    if not rows:
        return None
    df = pd.DataFrame([{
        "date": r[0], "open": float(r[1]), "high": float(r[2]), "close": float(r[3]),
    } for r in rows]).set_index("date")
    df["t_close"] = df["close"]
    df["next_open"] = df["open"].shift(-1)
    df["next_high"] = df["high"].shift(-1)
    df["next_close"] = df["close"].shift(-1)
    return df[["t_close", "next_open", "next_high", "next_close"]].reindex(index)


def train_window(engine, X_train, y_train, model_type):
    """One classifier (or xgb+lgbm pair) per threshold trained on close-to-close target."""
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


def proba(m, Xe):
    """Return (mean_prob, per_model_probs[list]) for a trained threshold entry."""
    if isinstance(m, float):
        return np.full(len(Xe), m), [np.full(len(Xe), m)]
    if isinstance(m, list):
        per = [e.predict_proba(Xe)[:, 1] for e in m]
        return np.mean(per, axis=0), per
    p = m.predict_proba(Xe)[:, 1]
    return p, [p]


def conviction_score(comp, agree, st_i, d200, rsi_i, d20,
                     atr_i, range_i, oh_freq2_i, rvol_i, volr_i, volz_i):
    """Volatility-led conviction for a next-day >= +2% move.

    A +2% move is primarily a VOLATILITY event: atr_ratio / range_pct / realized_vol and
    habitual-big-mover features (oh_freq2_20) lift P(+2%) ~1.6-1.8x, whereas trend/RSI
    features sit at ~1.0x (analyze_winners.py). So MAGNITUDE comes from volatility, while
    DIRECTION (up vs down) comes from the ML close-to-close probability; volume is a mild
    confirmation and a light trend tilt + overextension guard shape the up/down odds.
    """
    score = comp                            # ML directional probability (up tilt)
    score *= (0.7 + 0.3 * agree)            # model agreement (light)

    mult = 1.0
    # --- magnitude: volatility / habitual big-mover (the real +2% drivers) ---
    if atr_i is not None:       mult += 0.35 * float(np.clip((atr_i - 0.030) / 0.030, -1, 1.5))
    if range_i is not None:     mult += 0.20 * float(np.clip((range_i - 0.030) / 0.030, -1, 1.5))
    if oh_freq2_i is not None:  mult += 0.25 * float(np.clip((oh_freq2_i - 0.27) / 0.27, -1, 1.5))
    if rvol_i is not None:      mult += 0.15 * float(np.clip((rvol_i - 0.022) / 0.022, -1, 1.5))
    # --- volume confirmation (mild, ~1.15x) ---
    if volr_i is not None and volr_i >= 1.1:  mult += 0.10
    if volz_i is not None and volz_i > 0:     mult += 0.05
    # --- light directional tilt (trend ~1.0x for +2% freq, but biases direction up) ---
    if d200 is not None and d200 > 0:  mult += 0.05
    if st_i == 1:                      mult += 0.05
    # --- overextension guard (avoid blow-off tops that fade) ---
    if rsi_i is not None and rsi_i >= 75:  mult -= 0.15
    if d20 is not None and d20 > 0.15:     mult -= 0.10
    return score * max(0.3, mult)


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)
    symbols = liquid_symbols(db)
    sym_to_id = {s.symbol: s.id for s in
                 db.query(Stock.symbol, Stock.id).filter(Stock.symbol.in_(symbols)).all()}
    test_name = f"Conviction backtest (Top-5, 3 selectors, {K_DAYS}d OOS, model={MODEL})"
    start_job("backtest", total=len(symbols),
              message=f"{test_name} \u2014 training walk-forward models on {len(symbols)} liquid stocks")
    print(f"[CONVICTION TOP-5] universe={len(symbols)} | OOS last {K_DAYS} sessions | "
          f"model={MODEL}", flush=True)
    print(f"composite {COMPOSITE_WEIGHTS} (leaned to +2%/+3%)\n", flush=True)

    # date -> {sym: (comp, prod, conv, oc, cc, hi_hit2)}
    by_date = {}
    universe_oc = {}
    processed = skipped = 0
    for n_done, sym in enumerate(symbols, 1):
        if n_done % 25 == 0:
            set_progress(current=n_done,
                         message=f"{test_name} \u2014 trained {n_done}/{len(symbols)} (used {processed})")
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

        oc_t = outcomes(db, sym_to_id[sym], X.index)
        if oc_t is None:
            skipped += 1
            continue

        # close-to-close target, known only before the window
        tcl = oc_t["t_close"]
        ncl = oc_t["next_close"]
        cc_all = (ncl - tcl) / tcl
        train_idx = X.index[:test_start]
        y_train = cc_all.loc[train_idx].dropna()
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
        # composite + per-model P(+2%) for agreement
        comp = np.zeros(len(test))
        p2_models = None
        for th, w in COMPOSITE_WEIGHTS.items():
            mp, per = proba(models.get(th, 0.0), test)
            comp += w * mp
            if th == 0.02:
                p2_models = per
        if p2_models and len(p2_models) >= 2:
            agree = 1.0 - np.minimum(1.0, np.abs(p2_models[0] - p2_models[1]) / 0.5)
        else:
            agree = np.ones(len(test))

        no = oc_t["next_open"].reindex(test.index).values
        nh = oc_t["next_high"].reindex(test.index).values
        nc = oc_t["next_close"].reindex(test.index).values
        tc = oc_t["t_close"].reindex(test.index).values
        rsi = test["rsi"].values
        adx = test["adx"].values
        d20 = test["dist_ema_20"].values
        d50 = test["dist_ema_50"].values
        d200 = test["dist_ema_200"].values
        st = test["supertrend_dir"].values
        atrr = test["atr_ratio"].values
        ret5 = test["return_5d"].values
        rng = test["range_pct"].values
        ohf2 = test["oh_freq2_20"].values
        rvol = test["realized_vol_20"].values
        volr = test["volume_ratio"].values
        volz = test["vol_zscore_20"].values
        idx = test.index

        for i in range(len(test)):
            o, h, c, t0 = no[i], nh[i], nc[i], tc[i]
            if any(v is None or np.isnan(v) for v in (o, h, c, t0)) or o <= 0 or t0 <= 0:
                continue
            oc = (c - o) / o
            cc = (c - t0) / t0
            hi_hit2 = 1 if (h - o) / o >= 0.02 else 0
            universe_oc.setdefault(idx[i], []).append(oc)

            rsi_i = float(rsi[i]) if not np.isnan(rsi[i]) else None
            st_i = int(st[i]) if not np.isnan(st[i]) else None
            if PICK_EXCLUDE_SUPERTREND_DOWN and st_i == -1:
                continue
            if rsi_i is not None and rsi_i >= PICK_MAX_RSI:
                continue
            ml = float(comp[i])
            prod = pick_final_score(
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
            conv = conviction_score(
                ml, float(agree[i]), st_i,
                float(d200[i]) if not np.isnan(d200[i]) else None,
                rsi_i,
                float(d20[i]) if not np.isnan(d20[i]) else None,
                float(atrr[i]) if not np.isnan(atrr[i]) else None,
                float(rng[i]) if not np.isnan(rng[i]) else None,
                float(ohf2[i]) if not np.isnan(ohf2[i]) else None,
                float(rvol[i]) if not np.isnan(rvol[i]) else None,
                float(volr[i]) if not np.isnan(volr[i]) else None,
                float(volz[i]) if not np.isnan(volz[i]) else None,
            )
            by_date.setdefault(idx[i], {})[sym] = (ml, prod, conv, oc, cc, hi_hit2)
        processed += 1

    print(f"\nProcessed {processed} stocks ({skipped} skipped).\n", flush=True)
    set_progress(current=len(symbols),
                 message=f"{test_name} \u2014 scoring 3 selectors over last {K_DAYS} sessions")
    dates = sorted(by_date.keys())[-K_DAYS:]

    def report(rank_idx, label):
        tot = 0
        oc_all, cc_hit2, hi_hit2 = [], 0, 0
        daily_oc, bench_oc = [], []
        for d in dates:
            recs = list(by_date[d].items())
            if len(recs) < TOP_N:
                continue
            ranked = sorted(recs, key=lambda kv: kv[1][rank_idx], reverse=True)[:TOP_N]
            ocs = [v[3] for _, v in ranked]
            oc_all.extend(ocs)
            cc_hit2 += sum(1 for _, v in ranked if v[4] >= 0.02)
            hi_hit2 += sum(1 for _, v in ranked if v[5] == 1)
            tot += len(ranked)
            daily_oc.append(np.mean(ocs))
            bench_oc.append(np.mean(universe_oc.get(d, [0.0])))
        if not tot:
            print(f"== {label}: insufficient days ==\n"); return
        ndays = len(daily_oc)
        cum = np.prod([1 + m for m in daily_oc]) - 1
        bcum = np.prod([1 + m for m in bench_oc]) - 1
        print(f"== {label}  (top {TOP_N} x {ndays} days = {tot} picks) ==")
        print(f"  avg open->close          : {np.mean(oc_all)*100:+.3f}%   <- actionable hold")
        print(f"  % closed >= +2% (c2c)    : {cc_hit2/tot*100:.1f}%   <- the +2% target")
        print(f"  % hit +2% intraday high  : {hi_hit2/tot*100:.1f}%   <- +2% limit fills")
        print(f"  % green (open->close)    : {sum(1 for p in oc_all if p>0)/tot*100:.1f}%")
        print(f"  cumulative open->close   : {cum*100:+.2f}%  (universe {bcum*100:+.2f}%)")
        print(f"  edge vs universe         : {(np.mean(daily_oc)-np.mean(bench_oc))*100:+.3f}%/day\n")

    report(0, "A) PLAIN composite")
    report(1, "B) PRODUCTION pick_final_score")
    report(2, "C) CONVICTION multi-factor")

    print("===== CONVICTION TOP-5 PER DAY =====")
    for d in dates:
        recs = list(by_date[d].items())
        if len(recs) < TOP_N:
            continue
        top5 = sorted(recs, key=lambda kv: kv[1][2], reverse=True)[:TOP_N]
        print(f"\n{d}:")
        print(f"    {'#':<2}{'symbol':<14}{'conv':>7}{'O->C':>9}{'c2c':>9}{'hi2%':>6}")
        for rnk, (sym, v) in enumerate(top5, 1):
            print(f"    {rnk:<2}{sym:<14}{v[2]*100:>6.1f}{v[3]*100:>8.2f}%"
                  f"{v[4]*100:>8.2f}%{('Y' if v[5] else '-'):>6}")

    finish_job(message=f"{test_name} \u2014 complete ({processed} stocks, {len(dates)} sessions)")
    db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        fail_job(message=f"Conviction backtest failed: {str(e)[:120]}")
        raise
