"""
A/B BACKTEST — does (A) a market-regime gate or (B) an oversold-volatile tilt beat
the current production Top-5 (P(+3%) x volatility, hold-to-close)?

Everything is walk-forward / out-of-sample, reusing backtest_p3's training machinery so
the model and universe match production exactly. We rank candidates each day by a blend
and measure realized next-day CLOSE-TO-CLOSE returns (the locked target).

Variants:
  BASELINE      blend = (1-W)*pct[P(+3%)] + W*pct[vol]              (W = PICK_VOL_BLEND_WEIGHT)
                vol score = mean cross-sectional pctile of {atr_ratio, range_pct}

  B (reversion) blend = wp*pct[P3] + wv*pct[vol] + wo*pct[oversold]
                oversold = mean of (1-pct[rsi]), (1-pct[bb_pct])   -> beaten-down high-vol names
                sweep the oversold weight wo (taken out of the vol weight)

  A (regime)    same BASELINE ranking, but gate/size by a same-day BREADTH proxy known at
                decision time: breadth = share of evaluable names with close>EMA20 that day.
                  * split: hit-rate & return on STRONG vs WEAK breadth days
                  * gated: trade Top-5 only on strong-breadth days (skip weak) 
                  * sizer: Top-5 on strong days, Top-2 on weak days
                (breadth uses ONLY day-t features -> no look-ahead.)

  A+B           regime-gated AND oversold-tilted.

Run inside the backend container:
    docker compose exec -T backend python backtest_ab.py [K_DAYS] [TOP_N] [MODEL]
        K_DAYS=30  TOP_N=5  MODEL=xgb (default; ensemble matches prod but is slower)
"""
import sys
import numpy as np
import pandas as pd

from app.database import SessionLocal
from app.services.prediction import PredictionEngine
from app.services.screener import (
    MIN_PICK_PRICE, MIN_PICK_TURNOVER, PICK_LIQUIDITY_WINDOW_DAYS,
    PICK_MAX_RSI, PICK_EXCLUDE_SUPERTREND_DOWN, PICK_VOL_BLEND_WEIGHT,
)
from app.models.models import Stock
from backtest_p3 import (
    THRESHOLDS, RANK_TH, MIN_TRAIN_ROWS,
    liquid_symbols, cc_targets, train_window, proba_array, _pct_rank_arr,
)

K_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 5
MODEL = sys.argv[3] if len(sys.argv) > 3 else "xgb"
W = PICK_VOL_BLEND_WEIGHT   # production vol weight (0.50)


def _vol_score(recs):
    pa = _pct_rank_arr([r["atr_ratio"] for r in recs])
    pr = _pct_rank_arr([r["range_pct"] for r in recs])
    return (pa + pr) / 2.0


def _oversold_score(recs):
    # high when RSI is LOW and price is low in its Bollinger band (beaten down)
    prsi = _pct_rank_arr([r["rsi"] for r in recs])
    pbb = _pct_rank_arr([r["bb_pct"] for r in recs])
    return ((1 - prsi) + (1 - pbb)) / 2.0


def rank_blend(recs, top_n, wo=0.0):
    """wp*pct[P3] + wv*pct[vol] + wo*pct[oversold], with wp=1-W, wv=W-wo."""
    if len(recs) < top_n:
        return None
    p3 = _pct_rank_arr([r["p3"] for r in recs])
    vol = _vol_score(recs)
    wv = max(W - wo, 0.0)
    blend = (1 - W) * p3 + wv * vol + wo * _oversold_score(recs)
    order = np.argsort(-blend)[:top_n]
    return [recs[j] for j in order]


def _summ(picks_by_day, universe_cc, label):
    """picks_by_day: dict date->list[rec]. Return metrics dict."""
    daily, allp, hit2, tot = [], [], 0, 0
    bench = []
    for d, picks in picks_by_day.items():
        if not picks:
            continue
        rets = [p["cc"] for p in picks if p["cc"] is not None]
        if not rets:
            continue
        daily.append(np.mean(rets))
        allp.extend(rets)
        hit2 += sum(1 for v in rets if v >= 0.02)
        tot += len(rets)
        bench.append(np.mean(universe_cc.get(d, [0.0])))
    if not allp:
        return None
    cum = np.prod([1 + m for m in daily]) - 1
    bcum = np.prod([1 + m for m in bench]) - 1
    return {
        "label": label, "ndays": len(daily), "npicks": tot,
        "avg": np.mean(allp) * 100, "win": sum(1 for v in allp if v > 0) / tot * 100,
        "hit2": hit2 / tot * 100, "daily": np.mean(daily) * 100, "cum": cum * 100,
        "bench_cum": bcum * 100, "edge": (np.mean(daily) - np.mean(bench)) * 100,
    }


def _print(m):
    if not m:
        print("  (no data)")
        return
    print(f"  {m['label']:<34}{m['avg']:>+8.3f}%/pk {m['win']:>5.0f}%win "
          f"{m['hit2']:>5.0f}%≥2% {m['daily']:>+7.3f}%/d {m['cum']:>+8.2f}%cum "
          f"(edge {m['edge']:>+.3f}%/d, n={m['npicks']})")


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)
    symbols = liquid_symbols(db)
    sym_to_id = {s.symbol: s.id for s in
                 db.query(Stock.symbol, Stock.id).filter(Stock.symbol.in_(symbols)).all()}
    print(f"A/B backtest | universe={len(symbols)} | {K_DAYS}d OOS | model={MODEL} | TOP_N={TOP_N}\n",
          flush=True)

    by_date = {}         # date -> list[rec dict]
    universe_cc = {}     # date -> realized cc of all evaluable names
    breadth_raw = {}     # date -> list[1 if close>ema20 else 0]  (decision-time breadth)
    processed = skipped = 0
    for n_done, sym in enumerate(symbols, 1):
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
        base_rates = {th: float((y_train >= th).mean()) for th in THRESHOLDS}
        try:
            models = train_window(engine, X_train, y_train, MODEL)
        except Exception:
            skipped += 1
            continue
        test = X.iloc[test_start:n - 1]
        raw = {th: proba_array(models[th], test) for th in THRESHOLDS}
        cc = tg["cc"].reindex(test.index).values
        ch = tg["ch"].reindex(test.index).values

        def cv(col):
            return test[col].values if col in test.columns else np.full(len(test), np.nan)
        rsi = cv("rsi"); st = cv("supertrend_dir"); atr = cv("atr_ratio")
        rng = cv("range_pct"); bb = cv("bb_pct"); de20 = cv("dist_ema_20")
        idx = test.index
        for i in range(len(test)):
            r = cc[i]
            if r is None or np.isnan(r):
                continue
            universe_cc.setdefault(idx[i], []).append(float(r))
            if not np.isnan(de20[i]):
                breadth_raw.setdefault(idx[i], []).append(1 if de20[i] > 0 else 0)
            rsi_i = float(rsi[i]) if not np.isnan(rsi[i]) else None
            st_i = int(st[i]) if not np.isnan(st[i]) else None
            if PICK_EXCLUDE_SUPERTREND_DOWN and st_i == -1:
                continue
            if rsi_i is not None and rsi_i >= PICK_MAX_RSI:
                continue
            row_raw = {th: float(raw[th][i]) for th in THRESHOLDS}
            adj = engine._monotonic_with_base_rates(row_raw, base_rates)
            by_date.setdefault(idx[i], []).append({
                "sym": sym, "p3": adj[RANK_TH], "cc": float(r),
                "ch": float(ch[i]) if not np.isnan(ch[i]) else None,
                "rsi": rsi_i if rsi_i is not None else 50.0,
                "bb_pct": float(bb[i]) if not np.isnan(bb[i]) else 0.5,
                "atr_ratio": float(atr[i]) if not np.isnan(atr[i]) else np.nan,
                "range_pct": float(rng[i]) if not np.isnan(rng[i]) else np.nan,
            })
        processed += 1

    print(f"\nProcessed {processed} stocks ({skipped} skipped).", flush=True)
    dates = sorted(by_date.keys())[-K_DAYS:]
    breadth = {d: (np.mean(breadth_raw[d]) if breadth_raw.get(d) else 0.5) for d in dates}
    bvals = [breadth[d] for d in dates]
    bmed = float(np.median(bvals))
    print(f"Decision-time breadth (share close>EMA20): "
          f"min {min(bvals):.2f} / median {bmed:.2f} / max {max(bvals):.2f}\n", flush=True)

    # ---------- BASELINE ----------
    print("=" * 78)
    print("BASELINE — current production: Top-5 by P(+3%) x volatility, hold-to-close")
    print("=" * 78)
    base_picks = {d: rank_blend(by_date[d], TOP_N, wo=0.0) for d in dates}
    base_picks = {d: p for d, p in base_picks.items() if p}
    base_m = _summ(base_picks, universe_cc, "BASELINE (all days, Top-5)")
    _print(base_m)

    # ---------- B: oversold tilt sweep ----------
    print("\n" + "=" * 78)
    print("B) OVERSOLD-VOLATILE TILT — add oversold weight (taken from the vol weight)")
    print("=" * 78)
    best_b = None
    for wo in [0.0, 0.15, 0.25, 0.35, 0.50]:
        picks = {d: rank_blend(by_date[d], TOP_N, wo=wo) for d in dates}
        picks = {d: p for d, p in picks.items() if p}
        m = _summ(picks, universe_cc, f"oversold w={wo:.2f}")
        _print(m)
        if m and (best_b is None or m["cum"] > best_b[0]["cum"]):
            best_b = (m, wo, picks)

    # ---------- A: regime split + gate + sizer ----------
    print("\n" + "=" * 78)
    print("A) MARKET-REGIME — does decision-time breadth predict the day's hit-rate?")
    print("=" * 78)
    strong_days = [d for d in dates if breadth[d] >= bmed]
    weak_days = [d for d in dates if breadth[d] < bmed]
    sp = {d: base_picks[d] for d in strong_days if d in base_picks}
    wp = {d: base_picks[d] for d in weak_days if d in base_picks}
    _print(_summ(sp, universe_cc, f"STRONG-breadth days (>= {bmed:.2f})"))
    _print(_summ(wp, universe_cc, f"WEAK-breadth days (< {bmed:.2f})"))

    print("\n  --- regime STRATEGIES (vs baseline cum {:+.2f}%) ---".format(base_m["cum"]))
    # gated: trade only strong-breadth days
    _print(_summ(sp, universe_cc, "GATED: skip weak-breadth days"))
    # sizer: Top-5 strong, Top-2 weak
    sizer = {}
    for d in dates:
        recs = by_date[d]
        nn = TOP_N if breadth[d] >= bmed else max(2, TOP_N // 2)
        p = rank_blend(recs, nn, wo=0.0)
        if p:
            sizer[d] = p
    _print(_summ(sizer, universe_cc, f"SIZER: Top-{TOP_N} strong / Top-2 weak"))

    # ---------- A+B ----------
    print("\n" + "=" * 78)
    print("A+B — regime gate + best oversold tilt")
    print("=" * 78)
    wo_b = best_b[1] if best_b else 0.0
    ab = {}
    for d in dates:
        recs = by_date[d]
        nn = TOP_N if breadth[d] >= bmed else max(2, TOP_N // 2)
        p = rank_blend(recs, nn, wo=wo_b)
        if p:
            ab[d] = p
    _print(base_m)
    _print(best_b[0] if best_b else None)
    _print(_summ(ab, universe_cc, f"A+B sizer + oversold w={wo_b:.2f}"))

    # gated A+B (skip weak days entirely, oversold tilt on strong)
    abg = {d: rank_blend(by_date[d], TOP_N, wo=wo_b) for d in strong_days if d in by_date}
    abg = {d: p for d, p in abg.items() if p}
    _print(_summ(abg, universe_cc, f"A+B gated (skip weak) + oversold w={wo_b:.2f}"))

    print("\nNOTE: 'GATED/skip weak' trades fewer days, so compare cum% AND days traded.")
    print("Ship to production ONLY a variant that beats BASELINE cum% with similar coverage.\n")
    db.close()


if __name__ == "__main__":
    main()
