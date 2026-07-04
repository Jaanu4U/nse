"""
TOP-5 by P(+3%) backtest — "+3% is the high-priority ranking signal".

Strategy under test (exactly what the home-page +3% panel ranks by):
    on day t, for every liquid, non-excluded stock, rank by the model's
    next-day CLOSE-TO-CLOSE probability P((next_close-close)/close >= 3%);
    take the TOP 5; the realized outcome is the next-day close-to-close return
    (next_close - close)/close. We report how many of those Top-5 actually
    reached >= +1% / +2% / +3%.

Two phases:
  PHASE 1 (TODAY): print today's Top-5 by P(+3%) straight from the live DB
    predictions (the monotonicity-fixed values the website shows) so we can
    confirm the selection logic is exact BEFORE trusting the historical run.
  PHASE 2 (BACKTEST): walk-forward, out-of-sample. For each stock, train the
    four threshold classifiers ONLY on rows before the K-day test window
    (no look-ahead), predict P(+3%) with the SAME base-rate monotonic cap used
    in production, rank Top-5 per day, and measure realized +2%/+3% hit rates.

Universe + hard exclusions match the live screener: liquid tier
(MIN_PICK_PRICE / MIN_PICK_TURNOVER over PICK_LIQUIDITY_WINDOW_DAYS), drop
confirmed Supertrend downtrends and RSI>=85 blow-offs.

Run inside the backend container:
    docker compose exec -T backend python backtest_p3.py [K_DAYS] [TOP_N] [MODEL]
    K_DAYS = 30 (default)   TOP_N = 5 (default)   MODEL = ensemble (default) | xgb | lgbm
"""
import sys
import os
import json
import datetime
import numpy as np
import pandas as pd

from app.database import SessionLocal
from app.services.prediction import PredictionEngine
from app.services.screener import (
    MIN_PICK_PRICE, MIN_PICK_TURNOVER, PICK_LIQUIDITY_WINDOW_DAYS,
    PICK_MAX_RSI, PICK_EXCLUDE_SUPERTREND_DOWN, PICK_VOL_BLEND_WEIGHT,
)
from app.models.models import PriceDaily, Stock, Prediction, TechnicalIndicator
from sqlalchemy import func, text
from app.utils.job_progress import start_job, set_progress, finish_job, fail_job

K_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 5
MODEL = sys.argv[3] if len(sys.argv) > 3 else "ensemble"

# Where the 30-day scorecard JSON artifact is written for the live endpoint to serve.
SCORECARD_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "models", "strategy_scorecard_30d.json")

# Close-to-close thresholds (the production target). P(+3%) = the th=0.03 model.
THRESHOLDS = [0.01, 0.02, 0.03, 0.05]
RANK_TH = 0.03            # rank picks by this probability
RESULT_THS = [0.01, 0.02, 0.03]   # realized hit buckets to report
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


def cc_targets(db, stock_id, index):
    """Next-day realized returns aligned to the feature index.

    cc = today close -> next-day close   ((next_close-close)/close)
    ch = today close -> next-day HIGH    ((next_high -close)/close)
    oh = next-day open -> next-day HIGH  ((next_high-next_open)/next_open)
    """
    rows = db.query(PriceDaily.timestamp, PriceDaily.open, PriceDaily.high,
                    PriceDaily.low, PriceDaily.close)\
        .filter(PriceDaily.stock_id == stock_id).order_by(PriceDaily.timestamp).all()
    if not rows:
        return None
    df = pd.DataFrame([{"date": r[0], "open": float(r[1]), "high": float(r[2]),
                        "low": float(r[3]), "close": float(r[4])} for r in rows]).set_index("date")
    df["next_close"] = df["close"].shift(-1)
    df["next_high"] = df["high"].shift(-1)
    df["next_low"] = df["low"].shift(-1)
    df["next_open"] = df["open"].shift(-1)
    df["cc"] = (df["next_close"] - df["close"]) / df["close"]
    df["ch"] = (df["next_high"] - df["close"]) / df["close"]
    df["oh"] = (df["next_high"] - df["next_open"]) / df["next_open"]
    df["oc"] = (df["next_close"] - df["next_open"]) / df["next_open"]
    return df[["close", "next_open", "next_high", "next_low", "next_close",
               "cc", "ch", "oh", "oc"]].reindex(index)


def train_window(engine, X_train, y_train, model_type):
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


def proba_array(model, Xe):
    if isinstance(model, float):
        return np.full(len(Xe), model)
    if isinstance(model, list):
        return np.mean([e.predict_proba(Xe)[:, 1] for e in model], axis=0)
    return model.predict_proba(Xe)[:, 1]


# Live-available volatility drivers used by the production Top-5 blend.
LIVE_VOL_COLS = ["atr_ratio", "range_pct"]


def _pct_rank_arr(a):
    a = np.asarray(a, dtype=float)
    med = np.nanmedian(a)
    if not np.isfinite(med):
        med = 0.0
    a = np.where(np.isnan(a), med, a)
    order = a.argsort()
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(len(a))
    return ranks / max(len(a) - 1, 1)


def blend_rank(recs, top_n, weight, vol_cols=LIVE_VOL_COLS):
    """Order recs best-first by (1-w)*pctile[P(+3%)] + w*pctile[mean vol pctile].

    Mirrors screener._volatility_blend_rank so the backtest scorecard tracks exactly
    what the live Top-5 selects. rec[1]=P(+3%) rank prob, rec[12]=vol feature dict.
    """
    if not recs:
        return []
    pct_p3 = _pct_rank_arr([r[1] for r in recs])
    vol_pcts = [_pct_rank_arr([(r[12].get(c) if r[12] else np.nan) for r in recs])
                for c in vol_cols]
    vol_score = np.mean(vol_pcts, axis=0)
    blend = (1 - weight) * pct_p3 + weight * vol_score
    order = np.argsort(-blend)[:top_n]
    return [recs[j] for j in order]


# ---------------------------------------------------------------------------
# PHASE 1 — today's live Top-5 by P(+3%) (straight from the DB predictions)
# ---------------------------------------------------------------------------
def show_today(db):
    latest_pred_sq = (
        db.query(Prediction.stock_id.label("stock_id"),
                 func.max(Prediction.timestamp).label("ts"))
        .group_by(Prediction.stock_id).subquery()
    )
    latest_ind_sq = (
        db.query(TechnicalIndicator.stock_id.label("stock_id"),
                 func.max(TechnicalIndicator.timestamp).label("ts"))
        .group_by(TechnicalIndicator.stock_id).subquery()
    )
    latest_price_sq = (
        db.query(PriceDaily.stock_id.label("stock_id"),
                 func.max(PriceDaily.timestamp).label("ts"))
        .group_by(PriceDaily.stock_id).subquery()
    )
    latest = db.query(func.max(PriceDaily.timestamp)).scalar()
    cutoff = latest - datetime.timedelta(days=PICK_LIQUIDITY_WINDOW_DAYS)
    turnover_sq = (
        db.query(PriceDaily.stock_id.label("stock_id"),
                 func.avg(PriceDaily.close * PriceDaily.volume).label("avg_turnover"))
        .filter(PriceDaily.timestamp >= cutoff)
        .group_by(PriceDaily.stock_id).subquery()
    )
    q = db.query(Stock, Prediction, TechnicalIndicator, PriceDaily)\
        .join(latest_pred_sq, latest_pred_sq.c.stock_id == Stock.id)\
        .join(Prediction, (Prediction.stock_id == Stock.id) & (Prediction.timestamp == latest_pred_sq.c.ts))\
        .join(latest_price_sq, latest_price_sq.c.stock_id == Stock.id)\
        .join(PriceDaily, (PriceDaily.stock_id == Stock.id) & (PriceDaily.timestamp == latest_price_sq.c.ts))\
        .outerjoin(latest_ind_sq, latest_ind_sq.c.stock_id == Stock.id)\
        .outerjoin(TechnicalIndicator, (TechnicalIndicator.stock_id == Stock.id) & (TechnicalIndicator.timestamp == latest_ind_sq.c.ts))\
        .join(turnover_sq, turnover_sq.c.stock_id == Stock.id)\
        .filter(Stock.is_active == True)\
        .filter(PriceDaily.close >= MIN_PICK_PRICE)\
        .filter(turnover_sq.c.avg_turnover >= MIN_PICK_TURNOVER)
    cands = []
    for stock, pred, ind, price in q.all():
        rsi = float(ind.rsi) if (ind and ind.rsi is not None) else None
        st = int(ind.supertrend_dir) if (ind and getattr(ind, "supertrend_dir", None) is not None) else None
        if PICK_EXCLUDE_SUPERTREND_DOWN and st == -1:
            continue
        if rsi is not None and rsi >= PICK_MAX_RSI:
            continue
        close = float(price.close)
        atr_ratio = (float(ind.atr) / close) if (ind and ind.atr is not None and close) else np.nan
        range_pct = (float(price.high - price.low) / close) if (
            price.high is not None and price.low is not None and close) else np.nan
        cands.append((stock.symbol, float(pred.prob_plus_3) * 100, float(pred.prob_plus_2) * 100,
                      close, rsi, atr_ratio, range_pct))
    # rank by the SAME P(+3%)+volatility blend the live screener uses
    if cands:
        pct_p3 = _pct_rank_arr([c[1] for c in cands])
        pct_atr = _pct_rank_arr([c[5] for c in cands])
        pct_rng = _pct_rank_arr([c[6] for c in cands])
        vol_score = (pct_atr + pct_rng) / 2.0
        blend = (1 - PICK_VOL_BLEND_WEIGHT) * pct_p3 + PICK_VOL_BLEND_WEIGHT * vol_score
        cands = [cands[j] for j in np.argsort(-blend)]
    print("=" * 64)
    print(f"PHASE 1 — TODAY's Top-{TOP_N} by P(+3%)+vol blend (w={PICK_VOL_BLEND_WEIGHT})  "
          f"[as of {latest}]  universe={len(cands)}")
    print("=" * 64)
    print(f"  {'#':<2}{'symbol':<14}{'P(+3%)':>8}{'P(+2%)':>8}{'price':>10}{'RSI':>6}")
    for rnk, c in enumerate(cands[:TOP_N], 1):
        sym, p3, p2, px, rsi = c[0], c[1], c[2], c[3], c[4]
        print(f"  {rnk:<2}{sym:<14}{p3:>7.1f}%{p2:>7.1f}%{px:>10.1f}{(rsi or 0):>6.0f}")
    print("  (these are live predictions; their realized result is not known until tomorrow)\n", flush=True)


# ---------------------------------------------------------------------------
# PHASE 2 — out-of-sample walk-forward backtest
# ---------------------------------------------------------------------------
def main():
    db = SessionLocal()
    engine = PredictionEngine(db)

    show_today(db)

    symbols = liquid_symbols(db)
    sym_to_id = {s.symbol: s.id for s in
                 db.query(Stock.symbol, Stock.id).filter(Stock.symbol.in_(symbols)).all()}
    sym_to_name = {s.symbol: s.company_name for s in
                   db.query(Stock.symbol, Stock.company_name).filter(Stock.symbol.in_(symbols)).all()}
    test_name = f"P(+3%) Top-{TOP_N} backtest ({K_DAYS}d OOS, model={MODEL})"
    start_job("backtest", total=len(symbols),
              message=f"{test_name} — walk-forward training on {len(symbols)} liquid stocks")
    print("=" * 64)
    print(f"PHASE 2 — {test_name}")
    print(f"  universe={len(symbols)} liquid stocks | OOS last {K_DAYS} sessions | rank by P(+{int(RANK_TH*100)}%)")
    print("=" * 64, flush=True)

    by_date = {}        # date -> list of (sym, p3_rank, p2, realized_cc)
    universe_cc = {}    # date -> list of realized cc for all evaluable liquid names
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

        tg = cc_targets(db, sym_to_id[sym], X.index)
        if tg is None:
            skipped += 1
            continue

        y_all = tg["cc"]
        train_idx = X.index[:test_start]
        y_train = y_all.loc[train_idx].dropna()
        X_train = X.loc[y_train.index]
        if len(X_train) < MIN_TRAIN_ROWS:
            skipped += 1
            continue

        # historical base rates for the same monotonic cap used in production
        base_rates = {th: float((y_train >= th).mean()) for th in THRESHOLDS}

        try:
            models = train_window(engine, X_train, y_train, MODEL)
        except Exception:
            skipped += 1
            continue

        test = X.iloc[test_start:n - 1]
        # raw per-threshold probabilities for the test window
        raw = {th: proba_array(models[th], test) for th in THRESHOLDS}
        cc = tg["cc"].reindex(test.index).values
        ch = tg["ch"].reindex(test.index).values
        oh = tg["oh"].reindex(test.index).values
        oc = tg["oc"].reindex(test.index).values
        entry_p = tg["close"].reindex(test.index).values
        nopen = tg["next_open"].reindex(test.index).values
        nhigh = tg["next_high"].reindex(test.index).values
        nlow = tg["next_low"].reindex(test.index).values
        nclose = tg["next_close"].reindex(test.index).values
        rsi = test["rsi"].values
        st = test["supertrend_dir"].values
        # volatility features (the drivers of +2% moves per analyze_winners.py)
        VOL_COLS = ["atr_ratio", "range_pct", "oh_freq2_20", "realized_vol_20"]
        vols = {c: (test[c].values if c in test.columns else np.full(len(test), np.nan))
                for c in VOL_COLS}
        idx = test.index

        for i in range(len(test)):
            r = cc[i]
            if r is None or np.isnan(r):
                continue
            universe_cc.setdefault(idx[i], []).append(float(r))

            rsi_i = float(rsi[i]) if not np.isnan(rsi[i]) else None
            st_i = int(st[i]) if not np.isnan(st[i]) else None
            if PICK_EXCLUDE_SUPERTREND_DOWN and st_i == -1:
                continue
            if rsi_i is not None and rsi_i >= PICK_MAX_RSI:
                continue

            def _f(a):
                return float(a[i]) if not (a[i] is None or np.isnan(a[i])) else None

            row_raw = {th: float(raw[th][i]) for th in THRESHOLDS}
            adj = engine._monotonic_with_base_rates(row_raw, base_rates)
            ch_i = _f(ch)
            oh_i = _f(oh)
            oc_i = _f(oc)
            vol_i = {c: _f(vols[c]) for c in VOL_COLS}
            by_date.setdefault(idx[i], []).append(
                (sym, adj[RANK_TH], adj[0.02], float(r), ch_i, oh_i, oc_i,
                 _f(entry_p), _f(nopen), _f(nhigh), _f(nlow), _f(nclose), vol_i))
        processed += 1

    print(f"\nProcessed {processed} stocks ({skipped} skipped).\n", flush=True)
    dates = sorted(by_date.keys())[-K_DAYS:]

    # ---- aggregate Top-N ----
    tot = green = 0
    hit = {th: 0 for th in RESULT_THS}        # close->close hit buckets
    hit_ch = {th: 0 for th in RESULT_THS}     # close->next-high hit buckets
    hit_oh = {th: 0 for th in RESULT_THS}     # next-open->next-high hit buckets
    ch_vals = []
    oh_vals = []
    pcts_all = []
    daily_means = []
    bench_means = []
    per_day_rows = []
    # target-exit strategy: buy next OPEN, limit-sell at +T%, else exit at close
    EXIT_TS = [0.02, 0.03]
    exit_daily = {t: [] for t in EXIT_TS}     # per-day mean return of the rule
    exit_all = {t: [] for t in EXIT_TS}       # per-pick returns
    for d in dates:
        recs = by_date[d]
        if len(recs) < TOP_N:
            continue
        ranked = blend_rank(recs, TOP_N, PICK_VOL_BLEND_WEIGHT)
        pcts = [t[3] for t in ranked]
        chs = [t[4] for t in ranked if t[4] is not None]
        ohs = [t[5] for t in ranked if t[5] is not None]
        tot += len(pcts)
        green += sum(1 for p in pcts if p > 0)
        for th in RESULT_THS:
            hit[th] += sum(1 for p in pcts if p >= th)
            hit_ch[th] += sum(1 for p in chs if p >= th)
            hit_oh[th] += sum(1 for p in ohs if p >= th)
        pcts_all.extend(pcts)
        ch_vals.extend(chs)
        oh_vals.extend(ohs)
        daily_means.append(np.mean(pcts))
        bench_means.append(np.mean(universe_cc.get(d, [0.0])))
        per_day_rows.append((d, ranked, sum(1 for p in pcts if p >= 0.02)))
        # target-exit returns (need both open->high and open->close present)
        for t in EXIT_TS:
            day_rets = []
            for rec in ranked:
                ohv, ocv = rec[5], rec[6]
                if ohv is None or ocv is None:
                    continue
                day_rets.append(t if ohv >= t else ocv)
            if day_rets:
                exit_all[t].extend(day_rets)
                exit_daily[t].append(np.mean(day_rets))

    print("=" * 64)
    print(f"PHASE 2 RESULTS — Top-{TOP_N} ranked by P(+{int(RANK_TH*100)}%), realized next-day CLOSE-TO-CLOSE")
    print("=" * 64)
    if not tot:
        print("No days had enough picks.")
        finish_job(message=f"{test_name} — no data")
        db.close()
        return
    ndays = len(daily_means)
    port_cum = np.prod([1 + m for m in daily_means]) - 1
    bench_cum = np.prod([1 + m for m in bench_means]) - 1
    print(f"  sessions evaluated        : {ndays}")
    print(f"  total picks               : {tot}  (Top-{TOP_N} x {ndays} days)")
    print(f"  avg realized close->close : {np.mean(pcts_all)*100:+.3f}% / pick")
    print(f"  median realized           : {np.median(pcts_all)*100:+.3f}%")
    print(f"  picks closing GREEN       : {green:>4}  ({green/tot*100:.1f}%)")
    for th in RESULT_THS:
        print(f"  reached >= +{int(th*100)}%            : {hit[th]:>4}  ({hit[th]/tot*100:.1f}%)")
    print(f"  portfolio daily mean      : {np.mean(daily_means)*100:+.3f}% / day")
    print(f"  portfolio cumulative      : {port_cum*100:+.2f}%  over {ndays} days")
    print(f"  universe baseline cumul.  : {bench_cum*100:+.2f}%  (buy whole liquid universe)")
    print(f"  edge vs universe          : {(np.mean(daily_means)-np.mean(bench_means))*100:+.3f}% / day")
    avg_per_day = (sum(r[2] for r in per_day_rows) / ndays) if ndays else 0
    print(f"  avg #picks/day reaching +2% : {avg_per_day:.2f} of {TOP_N}\n")

    # ---- intraday-reach measures (touch the target sometime next day) ----
    print("=" * 64)
    print("INTRADAY-REACH — did the pick TOUCH the target next day (not just close)?")
    print("=" * 64)
    nch = len(ch_vals)
    noh = len(oh_vals)
    print(f"  CLOSE -> next-day HIGH   (avg {np.mean(ch_vals)*100:+.3f}% / pick, n={nch})")
    for th in RESULT_THS:
        c = hit_ch[th]
        print(f"    touched >= +{int(th*100)}%        : {c:>4}  ({c/nch*100:.1f}%)")
    print(f"  next OPEN -> next-day HIGH (avg {np.mean(oh_vals)*100:+.3f}% / pick, n={noh})")
    for th in RESULT_THS:
        c = hit_oh[th]
        print(f"    touched >= +{int(th*100)}%        : {c:>4}  ({c/noh*100:.1f}%)")
    avg_ch2 = sum(sum(1 for v in [t[4] for t in r[1]] if v is not None and v >= 0.02)
                  for r in per_day_rows) / ndays if ndays else 0
    avg_oh2 = sum(sum(1 for v in [t[5] for t in r[1]] if v is not None and v >= 0.02)
                  for r in per_day_rows) / ndays if ndays else 0
    print(f"  avg #picks/day touching +2% (close->high) : {avg_ch2:.2f} of {TOP_N}")
    print(f"  avg #picks/day touching +2% (open->high)  : {avg_oh2:.2f} of {TOP_N}\n")

    # ---- honest strategy comparison: hold-to-close vs buy-open/target-exit ----
    print("=" * 64)
    print("STRATEGY COMPARISON — same Top-5 picks, different EXIT rule")
    print("  A) hold to close      : buy ~prev close, sell at next close (cc)")
    print("  B) buy open, target T : buy next open, limit-sell at +T% else close")
    print("     (B assumes a resting limit order fills at +T% when high>=target;")
    print("      no slippage/fees; entry at open is realistic since picks are AM)")
    print("=" * 64)
    cc_cum = np.prod([1 + m for m in daily_means]) - 1
    print(f"  A) hold-to-close   : {np.mean(pcts_all)*100:+.3f}%/pick   "
          f"daily {np.mean(daily_means)*100:+.3f}%   cumulative {cc_cum*100:+.2f}%")
    for t in EXIT_TS:
        if not exit_all[t]:
            continue
        e_cum = np.prod([1 + m for m in exit_daily[t]]) - 1
        win = sum(1 for v in exit_all[t] if v > 0) / len(exit_all[t]) * 100
        hit_t = sum(1 for v in exit_all[t] if v >= t) / len(exit_all[t]) * 100
        print(f"  B) target +{int(t*100)}%      : {np.mean(exit_all[t])*100:+.3f}%/pick   "
              f"daily {np.mean(exit_daily[t])*100:+.3f}%   cumulative {e_cum*100:+.2f}%"
              f"   (filled at target {hit_t:.0f}%, win {win:.0f}%)")
    print()

    # ---- SCALE-OUT: sell a FRACTION at the intraday target, hold the rest to close ----
    #   entry = ~prev close (the hold-to-close strategy). For each pick:
    #     sold leg  : if close->high (ch) >= T  -> locks +T ; else exits at close (cc)
    #     held leg  : always exits at close (cc)  [keeps the fat tail]
    #   pick return = frac*sold_leg + (1-frac)*cc
    #   This directly answers "I want my +2% on more picks" WITHOUT killing the
    #   big winners, because only `frac` of each position is capped.
    print("=" * 64)
    print("SCALE-OUT — sell FRAC at +T% intraday (close->high), hold rest to close")
    print("  pick = frac*(+T if next-high>=+T else cc) + (1-frac)*cc ; entry ~prev close")
    print("  goal: lock the +2% you wanted on most picks, still ride the fat tail")
    print("=" * 64)
    print(f"  {'rule':<22}{'avg/pick':>10}{'daily':>9}{'cumul':>10}{'win%':>7}{'hit+2%':>8}")

    def _scaleout_return(rec, frac, T):
        # rec = (sym,p3,p2,cc,ch,oh,oc,entry,n_open,n_high,n_low,n_close,vol)
        cc_i, ch_i = rec[3], rec[4]
        if cc_i is None:
            return None
        sold = T if (ch_i is not None and ch_i >= T) else cc_i
        return frac * sold + (1 - frac) * cc_i

    SCALE_RULES = [(0.5, 0.02), (0.5, 0.03), (0.7, 0.02), (1.0, 0.02)]
    # baseline row first
    base_cum2 = np.prod([1 + m for m in daily_means]) - 1
    print(f"  {'hold-to-close':<22}{np.mean(pcts_all)*100:>+9.3f}%{np.mean(daily_means)*100:>+8.3f}%"
          f"{base_cum2*100:>+9.2f}%{sum(1 for v in pcts_all if v>0)/len(pcts_all)*100:>6.0f}%"
          f"{sum(1 for v in pcts_all if v>=0.02)/len(pcts_all)*100:>7.0f}%")
    for frac, T in SCALE_RULES:
        d_means, all_rets = [], []
        for d, ranked, _n2 in per_day_rows:
            day_rets = [r for r in (_scaleout_return(rec, frac, T) for rec in ranked) if r is not None]
            if day_rets:
                d_means.append(np.mean(day_rets))
                all_rets.extend(day_rets)
        if not all_rets:
            continue
        cum = np.prod([1 + m for m in d_means]) - 1
        win = sum(1 for v in all_rets if v > 0) / len(all_rets) * 100
        hit2 = sum(1 for v in all_rets if v >= 0.02) / len(all_rets) * 100
        label = f"sell {int(frac*100)}% @+{int(T*100)}%"
        print(f"  {label:<22}{np.mean(all_rets)*100:>+9.3f}%{np.mean(d_means)*100:>+8.3f}%"
              f"{cum*100:>+9.2f}%{win:>6.0f}%{hit2:>7.0f}%")
    print()

    # ---- gap-aware STOP-LOSS sweep on the HOLD-TO-CLOSE strategy ----
    print("=" * 64)
    print("STOP-LOSS SWEEP — hold-to-close + protective stop (GAP-AWARE fills)")
    print("  buy at entry (~prev close); stop price = entry*(1 - S).")
    print("  next session: if OPEN <= stop -> exit at OPEN (gap through stop, worse than -S);")
    print("  elif LOW <= stop -> exit at STOP (-S exactly); else hold to CLOSE (cc).")
    print("  no fees/slippage, but gap-downs are filled at the open (worst-case realistic).")
    print("=" * 64)
    STOP_LEVELS = [0.03, 0.04, 0.05, 0.06, 0.08]

    def _stopped_return(rec, stop):
        # rec = (sym,p3,p2,cc,ch,oh,oc,entry,n_open,n_high,n_low,n_close)
        cc_i, entry, n_open, n_low = rec[3], rec[7], rec[8], rec[10]
        if cc_i is None or entry is None or entry <= 0:
            return None, None
        stop_price = entry * (1 - stop)
        if n_open is not None and n_open <= stop_price:
            return (n_open - entry) / entry, "gap"   # gap through stop, fill at open
        if n_low is not None and n_low <= stop_price:
            return -stop, "stop"                      # intraday stop fill at -S
        return cc_i, "hold"                           # no stop, hold to close

    base_all = pcts_all
    base_daily = daily_means
    base_cum = np.prod([1 + m for m in base_daily]) - 1
    print(f"  baseline no stop : {np.mean(base_all)*100:+.3f}%/pick   "
          f"daily {np.mean(base_daily)*100:+.3f}%   cumulative {base_cum*100:+.2f}%   "
          f"win {sum(1 for v in base_all if v>0)/len(base_all)*100:.0f}%")
    for s in STOP_LEVELS:
        d_means, all_rets = [], []
        n_gap = n_stop = 0
        for d, ranked, _n2 in per_day_rows:
            day_rets = []
            for rec in ranked:
                r, kind = _stopped_return(rec, s)
                if r is None:
                    continue
                day_rets.append(r)
                if kind == "gap":
                    n_gap += 1
                elif kind == "stop":
                    n_stop += 1
            if day_rets:
                d_means.append(np.mean(day_rets))
                all_rets.extend(day_rets)
        if not all_rets:
            continue
        cum = np.prod([1 + m for m in d_means]) - 1
        win = sum(1 for v in all_rets if v > 0) / len(all_rets) * 100
        hit2 = sum(1 for v in all_rets if v >= 0.02) / len(all_rets) * 100
        worst = min(all_rets) * 100
        print(f"  stop -{int(s*100)}%        : {np.mean(all_rets)*100:+.3f}%/pick   "
              f"daily {np.mean(d_means)*100:+.3f}%   cumulative {cum*100:+.2f}%   "
              f"win {win:.0f}%  hit+2% {hit2:.0f}%  worst {worst:+.1f}%  "
              f"(stopped {n_stop+n_gap}/{len(all_rets)}: {n_stop} at-stop, {n_gap} gap-thru)")
    print()

    # ---- SELECTION VARIANT: volatility-tilted Top-5 (re-rank the candidate pool) ----
    print("=" * 64)
    print("SELECTION VARIANT — re-rank candidates by P(+3%) BLENDED with VOLATILITY")
    print("  vol score = mean cross-sectional percentile of")
    print("    {atr_ratio, range_pct, oh_freq2_20, realized_vol_20}  (the +2% drivers)")
    print("  blend(w) = (1-w)*pct[P(+3%)] + w*pct[vol];  w=0.00 == current pure-P(+3%) ranking")
    print("  same universe/exclusions, HOLD-TO-CLOSE exit, realized next-day cc.")
    print("=" * 64)
    VOL_COLS = ["atr_ratio", "range_pct", "oh_freq2_20", "realized_vol_20"]
    # subset actually persisted in the live DB (TechnicalIndicator.atr + price bar)
    LIVE_VOL_COLS = ["atr_ratio", "range_pct"]

    def _pct_rank(a):
        a = np.asarray(a, dtype=float)
        med = np.nanmedian(a) if np.isfinite(np.nanmedian(a)) else 0.0
        a = np.where(np.isnan(a), med, a)
        order = a.argsort()
        ranks = np.empty(len(a), dtype=float)
        ranks[order] = np.arange(len(a))
        return ranks / max(len(a) - 1, 1)

    # candidate days with at least TOP_N names (reuse the same trading dates)
    var_dates = [d for d in dates if len(by_date[d]) >= TOP_N]
    WEIGHTS = [0.0, 0.25, 0.50, 0.75, 1.0]

    def _run_blend(vol_cols, label):
        print(f"  --- vol features = {label} ---")
        print(f"  {'w':>5} {'avg/pick':>9} {'daily':>8} {'cumul':>9} {'win%':>6} "
              f"{'hit+2%':>7} {'hit+3%':>7} {'#+2/day':>8}")
        for w in WEIGHTS:
            d_means, all_cc, hit2c, hit3c, npick = [], [], 0, 0, 0
            per_day_2 = []
            for d in var_dates:
                recs = by_date[d]
                p3 = [rec[1] for rec in recs]
                vol_feats = {c: [(rec[12].get(c) if rec[12] else None) for rec in recs]
                             for c in vol_cols}
                pct_p3 = _pct_rank(p3)
                vol_pcts = [_pct_rank(vol_feats[c]) for c in vol_cols]
                vol_score = np.mean(vol_pcts, axis=0)
                blend = (1 - w) * pct_p3 + w * vol_score
                top = np.argsort(-blend)[:TOP_N]
                day_cc = [recs[j][3] for j in top if recs[j][3] is not None]
                if not day_cc:
                    continue
                d_means.append(np.mean(day_cc))
                all_cc.extend(day_cc)
                hit2c += sum(1 for c in day_cc if c >= 0.02)
                hit3c += sum(1 for c in day_cc if c >= 0.03)
                npick += len(day_cc)
                per_day_2.append(sum(1 for c in day_cc if c >= 0.02))
            if not all_cc:
                continue
            cum = np.prod([1 + m for m in d_means]) - 1
            win = sum(1 for c in all_cc if c > 0) / len(all_cc) * 100
            print(f"  {w:>5.2f} {np.mean(all_cc)*100:>+8.3f}% {np.mean(d_means)*100:>+7.3f}% "
                  f"{cum*100:>+8.2f}% {win:>5.0f}% {hit2c/npick*100:>6.1f}% "
                  f"{hit3c/npick*100:>6.1f}% {np.mean(per_day_2):>8.2f}")

    _run_blend(VOL_COLS, "4-feature {atr_ratio,range_pct,oh_freq2_20,realized_vol_20}")
    _run_blend(LIVE_VOL_COLS, "2-feature LIVE-AVAILABLE {atr_ratio,range_pct}")
    print("  (w=0.00 should ~match the headline hold-to-close; higher w trades direction for vol)\n")

    print("=" * 64)
    print(f"PER-DAY Top-{TOP_N} (P3=rank prob, cc=close->close, ch=close->high, oh=open->high)")
    print("=" * 64)
    for d, ranked, n2 in per_day_rows:
        print(f"\n{d}   ({n2}/{TOP_N} closed >= +2%)")
        print(f"    {'#':<2}{'symbol':<14}{'P(+3%)':>8}{'cc':>9}{'ch':>9}{'oh':>9}")
        for rnk, rec in enumerate(ranked, 1):
            sym, p3, pct, chv, ohv = rec[0], rec[1], rec[3], rec[4], rec[5]
            mark = "✓" if pct >= 0.02 else " "
            chs = f"{chv*100:>8.2f}%" if chv is not None else f"{'—':>9}"
            ohs = f"{ohv*100:>8.2f}%" if ohv is not None else f"{'—':>9}"
            print(f"  {mark} {rnk:<2}{sym:<14}{p3*100:>7.1f}%{pct*100:>8.2f}%{chs}{ohs}")

    # ---- write 30-day scorecard JSON artifact for the live endpoint ----
    try:
        cal = [r[0] for r in db.query(PriceDaily.timestamp)
               .distinct().order_by(PriceDaily.timestamp).all()]
        next_of = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}

        def _to_date(d):
            return d.date() if hasattr(d, "date") else d

        def _outcome(cc):
            if cc is None:
                return "PENDING"
            if cc > 0.001:
                return "WIN"
            if cc < -0.001:
                return "LOSS"
            return "FLAT"

        days_json = []
        for d, ranked, _n2 in per_day_rows:
            pd_date = _to_date(d)
            res_date = next_of.get(pd_date)
            picks = []
            for rec in ranked:
                (sym, p3, p2, cc, ch, oh, oc,
                 entry, n_open, n_high, n_low, n_close) = rec[:12]
                picks.append({
                    "symbol": sym,
                    "company_name": sym_to_name.get(sym, sym),
                    "entry": entry,
                    "prob_plus_3": p3,
                    "prob_plus_2": p2,
                    "next_open": n_open,
                    "next_high": n_high,
                    "next_low": n_low,
                    "next_close": n_close,
                    "cc": cc,
                    "ch": ch,
                    "oh": oh,
                    "outcome": _outcome(cc),
                })
            day_ccs = [p["cc"] for p in picks if p["cc"] is not None]
            days_json.append({
                "pick_date": pd_date.isoformat(),
                "result_date": res_date.isoformat() if res_date else None,
                "pending": False,
                "picks": picks,
                "summary": {
                    "scored": len(day_ccs),
                    "avg_cc": float(np.mean(day_ccs)) if day_ccs else None,
                    "green": sum(1 for c in day_ccs if c > 0),
                    "hit2": sum(1 for c in day_ccs if c >= 0.02),
                    "hit3": sum(1 for c in day_ccs if c >= 0.03),
                },
            })
        days_json.reverse()  # newest first

        artifact = {
            "source": "backtest_p3",
            "model": MODEL,
            "top_n": TOP_N,
            "k_days": K_DAYS,
            "generated_at": datetime.datetime.now().isoformat(),
            "overall": {
                "scored": tot,
                "avg_cc": float(np.mean(pcts_all)) if pcts_all else None,
                "green": green,
                "win_rate": (green / tot) if tot else None,
                "hit2": hit[0.02],
                "hit2_rate": (hit[0.02] / tot) if tot else None,
                "hit3": hit[0.03],
                "hit3_rate": (hit[0.03] / tot) if tot else None,
            },
            "days": days_json,
        }
        os.makedirs(os.path.dirname(SCORECARD_JSON), exist_ok=True)
        with open(SCORECARD_JSON, "w") as f:
            json.dump(artifact, f, indent=2)
        print(f"\nWrote scorecard artifact -> {SCORECARD_JSON} ({len(days_json)} days)", flush=True)
    except Exception as e:
        print(f"\n[warn] failed to write scorecard artifact: {e}", flush=True)

    finish_job(message=f"{test_name} — complete ({processed} stocks, {ndays} sessions)")
    db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        fail_job(message=f"P(+3%) backtest failed: {str(e)[:120]}")
        raise
