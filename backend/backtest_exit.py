"""
EXIT-TIMING BACKTEST — can a smarter exit beat plain hold-to-close on the SAME Top-5?

Motivation (live observation): a large share of Top-5 picks TOUCH +2% intraday and then
fade into the close. This script keeps the EXACT production selection (Top-5 by
P(+3%) x volatility, walk-forward OOS) and only changes the EXIT, measuring realized
return per rule so we can see if intraday timing leaves money on the table.

HONEST DATA LIMIT: we have DAILY bars only (open/high/low/close), NOT the intraday path.
So for any rule whose outcome depends on whether the HIGH or the LOW came first
(trailing / breakeven-after-touch), we report a BEST-case and WORST-case bound. The
truth lies between them; a rule is only worth shipping if even its WORST bound beats
hold-to-close. Path-clean rules (fixed target, scale-out, sell-at-open, hard stop)
have a single exact number.

Universe, model, exclusions and the Top-5 blend all match production (imported from
backtest_p3 / screener), so the comparison is apples-to-apples.

Run inside the backend container:
    docker compose exec -T backend python backtest_exit.py [K_DAYS] [TOP_N] [MODEL]
        K_DAYS=30  TOP_N=5  MODEL=xgb (default; ensemble matches prod but slower)
"""
import sys
import numpy as np

from app.database import SessionLocal
from app.services.prediction import PredictionEngine
from app.services.screener import (
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
W = PICK_VOL_BLEND_WEIGHT


def _select_top(recs, top_n):
    """Production parity: (1-W)*pct[P(+3%)] + W*mean(pct[atr_ratio],pct[range_pct])."""
    if len(recs) < top_n:
        return None
    p3 = _pct_rank_arr([r["p3"] for r in recs])
    pa = _pct_rank_arr([r["atr_ratio"] for r in recs])
    pr = _pct_rank_arr([r["range_pct"] for r in recs])
    blend = (1 - W) * p3 + W * ((pa + pr) / 2.0)
    order = np.argsort(-blend)[:top_n]
    return [recs[j] for j in order]


# ---- per-pick excursions vs entry (entry = day-t close) ----
def _exc(p):
    e = p["entry"]
    if not e or e <= 0:
        return None

    def f(v):
        return (v - e) / e if (v is not None) else None
    return {"o": f(p["nopen"]), "h": f(p["nhigh"]), "l": f(p["nlow"]), "c": f(p["nclose"])}


# ---- exit rules: each returns realized return fraction, or None if undeterminable ----
def r_hold(x):
    return x["c"]


def r_target(x, T):                       # path-clean: limit sell fills when high >= T
    if x["h"] is not None and x["h"] >= T:
        return T
    return x["c"]


def r_scaleout(x, frac, T):               # path-clean: sell `frac` at +T, rest at close
    if x["c"] is None:
        return None
    if x["h"] is not None and x["h"] >= T:
        return frac * T + (1 - frac) * x["c"]
    return x["c"]


def r_open(x):                            # sell at next open (does it gap then fade?)
    return x["o"]


def r_stop(x, S):                         # gap-aware hard stop at -S
    if x["o"] is not None and x["o"] <= -S:
        return x["o"]
    if x["l"] is not None and x["l"] <= -S:
        return -S
    return x["c"]


def r_trail_best(x, A, floor):
    """Breakeven/floor stop armed after touching +A. BEST path (high before low):
    losers that touched +A and closed below the floor exit at the floor; winners run."""
    if x["c"] is None:
        return None
    if x["h"] is not None and x["h"] >= A:       # armed
        return max(x["c"], floor)
    return x["c"]


def r_trail_worst(x, A, floor):
    """WORST path: any name that armed (+A) AND traded down to the floor is assumed
    stopped at the floor, even if it closed higher (peak surrendered)."""
    if x["c"] is None:
        return None
    armed = x["h"] is not None and x["h"] >= A
    touched = x["l"] is not None and x["l"] <= floor
    if armed and touched:
        return floor
    return x["c"]


def evaluate(picks_by_day, universe_cc, rule, label):
    daily, allp, hit2, tot, wins = [], [], 0, 0, 0
    bench = []
    for d in sorted(picks_by_day.keys()):
        picks = picks_by_day[d]
        if not picks:
            continue
        rets = []
        for p in picks:
            x = _exc(p)
            if x is None:
                continue
            rv = rule(x)
            if rv is None:
                continue
            rets.append(rv)
        if not rets:
            continue
        daily.append(np.mean(rets))
        allp.extend(rets)
        hit2 += sum(1 for v in rets if v >= 0.02)
        wins += sum(1 for v in rets if v > 0)
        tot += len(rets)
        bench.append(np.mean(universe_cc.get(d, [0.0])))
    if not allp:
        return None
    cum = np.prod([1 + m for m in daily]) - 1
    bcum = np.prod([1 + m for m in bench]) - 1
    return {
        "label": label, "ndays": len(daily), "npicks": tot,
        "avg": np.mean(allp) * 100, "win": wins / tot * 100,
        "hit2": hit2 / tot * 100, "daily": np.mean(daily) * 100,
        "cum": cum * 100, "bench_cum": bcum * 100,
        "edge": (np.mean(daily) - np.mean(bench)) * 100,
    }


def _print(m):
    if not m:
        print("  (no data)")
        return
    print(f"  {m['label']:<40}{m['avg']:>+7.3f}%/pk {m['win']:>4.0f}%win "
          f"{m['hit2']:>4.0f}%≥2% {m['cum']:>+8.2f}%cum (edge {m['edge']:>+.3f}%/d)")


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)
    symbols = liquid_symbols(db)
    sym_to_id = {s.symbol: s.id for s in
                 db.query(Stock.symbol, Stock.id).filter(Stock.symbol.in_(symbols)).all()}
    print(f"EXIT-timing backtest | universe={len(symbols)} | {K_DAYS}d OOS | "
          f"model={MODEL} | TOP_N={TOP_N}\n", flush=True)

    by_date = {}
    universe_cc = {}
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
        entry = tg["close"].reindex(test.index).values
        nopen = tg["next_open"].reindex(test.index).values
        nhigh = tg["next_high"].reindex(test.index).values
        nlow = tg["next_low"].reindex(test.index).values
        nclose = tg["next_close"].reindex(test.index).values

        def cv(col):
            return test[col].values if col in test.columns else np.full(len(test), np.nan)
        rsi = cv("rsi"); st = cv("supertrend_dir"); atr = cv("atr_ratio"); rng = cv("range_pct")
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
            row_raw = {th: float(raw[th][i]) for th in THRESHOLDS}
            adj = engine._monotonic_with_base_rates(row_raw, base_rates)

            def f(a):
                return float(a[i]) if not (a[i] is None or np.isnan(a[i])) else None
            by_date.setdefault(idx[i], []).append({
                "sym": sym, "p3": adj[RANK_TH],
                "atr_ratio": float(atr[i]) if not np.isnan(atr[i]) else np.nan,
                "range_pct": float(rng[i]) if not np.isnan(rng[i]) else np.nan,
                "entry": f(entry), "nopen": f(nopen), "nhigh": f(nhigh),
                "nlow": f(nlow), "nclose": f(nclose),
            })
        processed += 1

    print(f"\nProcessed {processed} stocks ({skipped} skipped).", flush=True)
    dates = sorted(by_date.keys())[-K_DAYS:]

    # Build the Top-5 picks per day (production parity), then study only those.
    picks_by_day = {}
    for d in dates:
        sel = _select_top(by_date[d], TOP_N)
        if sel:
            picks_by_day[d] = sel

    # ---------- OPPORTUNITY: how much do the picks give back into the close? ----------
    mfe, mae, clo, opn = [], [], [], []
    touched2 = faded_after_touch = closed_red = 0
    giveback = []
    n = 0
    for d, picks in picks_by_day.items():
        for p in picks:
            x = _exc(p)
            if x is None or x["c"] is None or x["h"] is None:
                continue
            n += 1
            mfe.append(x["h"]); mae.append(x["l"] if x["l"] is not None else 0.0)
            clo.append(x["c"]); opn.append(x["o"] if x["o"] is not None else 0.0)
            giveback.append(x["h"] - x["c"])
            if x["h"] >= 0.02:
                touched2 += 1
                if x["c"] < 0.02:
                    faded_after_touch += 1
            if x["c"] < 0:
                closed_red += 1
    print("\n" + "=" * 78)
    print("OPPORTUNITY — Top-5 picks: intraday peak vs the close (n={} picks)".format(n))
    print("=" * 78)
    if n:
        print(f"  avg next-open   {np.mean(opn)*100:>+6.2f}%      "
              f"avg HIGH (MFE) {np.mean(mfe)*100:>+6.2f}%")
        print(f"  avg LOW  (MAE)  {np.mean(mae)*100:>+6.2f}%      "
              f"avg CLOSE      {np.mean(clo)*100:>+6.2f}%")
        print(f"  touched +2% intraday: {touched2/n*100:>4.0f}%   "
              f"of those, closed < +2% (faded): {faded_after_touch/max(touched2,1)*100:>4.0f}%")
        print(f"  closed RED: {closed_red/n*100:>4.0f}%        "
              f"avg give-back (HIGH - CLOSE): {np.mean(giveback)*100:>+5.2f}%")
        print("  -> give-back is the theoretical max an exit rule could rescue; a real rule")
        print("     captures only part of it and pays for it by capping the fat-tail winners.")

    # ---------- BASELINE ----------
    print("\n" + "=" * 78)
    print("BASELINE — hold to next close (current production)")
    print("=" * 78)
    base = evaluate(picks_by_day, universe_cc, r_hold, "HOLD-TO-CLOSE")
    _print(base)
    bcum = base["cum"] if base else 0.0

    # ---------- PATH-CLEAN exit rules (single exact number) ----------
    print("\n" + "=" * 78)
    print("PATH-CLEAN exits (exact; high/low order does not matter)")
    print("=" * 78)
    for T in (0.015, 0.02, 0.03):
        _print(evaluate(picks_by_day, universe_cc, lambda x, T=T: r_target(x, T),
                        f"FIXED TARGET +{T*100:.1f}% (else close)"))
    print()
    for frac, T in ((0.5, 0.02), (0.5, 0.03), (0.7, 0.02)):
        _print(evaluate(picks_by_day, universe_cc, lambda x, f=frac, T=T: r_scaleout(x, f, T),
                        f"SCALE-OUT {int(frac*100)}% @+{T*100:.0f}%, rest to close"))
    print()
    _print(evaluate(picks_by_day, universe_cc, r_open, "SELL AT NEXT OPEN (gap capture)"))
    print()
    for S in (0.05, 0.06):
        _print(evaluate(picks_by_day, universe_cc, lambda x, S=S: r_stop(x, S),
                        f"HARD STOP -{S*100:.0f}% (gap-aware), else close"))

    # ---------- PATH-AMBIGUOUS: breakeven/floor stop after touching +A ----------
    print("\n" + "=" * 78)
    print("TRAILING / BREAKEVEN-after-touch — BEST & WORST path bounds (ambiguous)")
    print("=" * 78)
    print("  Rule: once price touches +A%, set a stop at the floor; otherwise hold to close.")
    print("  Ship only if the WORST bound still beats HOLD-TO-CLOSE (+{:.2f}% cum).\n".format(bcum))
    for A, floor in ((0.02, 0.0), (0.02, 0.005), (0.015, 0.0), (0.025, 0.01)):
        b = evaluate(picks_by_day, universe_cc, lambda x, A=A, fl=floor: r_trail_best(x, A, fl),
                     f"touch +{A*100:.1f}% -> floor +{floor*100:.1f}%  [BEST path]")
        w = evaluate(picks_by_day, universe_cc, lambda x, A=A, fl=floor: r_trail_worst(x, A, fl),
                     f"touch +{A*100:.1f}% -> floor +{floor*100:.1f}%  [WORST path]")
        _print(b)
        _print(w)
        print()

    print("NOTE: realized intraday capture needs an intraday feed; daily bars can only")
    print("bound the trailing rules. Keep production hold-to-close unless a WORST bound wins.")


if __name__ == "__main__":
    main()
