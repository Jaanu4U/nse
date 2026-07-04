"""
REAL TRAILING-STOP BACKTEST — replays exit rules on actual INTRADAY (5-min) paths.

backtest_exit.py could only BOUND trailing/breakeven rules (daily bars don't reveal
whether the high or the low came first). This script fetches real 5-minute bars for the
picked names on their holding session and walks them bar-by-bar, so the trailing and
breakeven-after-touch rules get an EXACT realized return instead of a best/worst band.

Selection is the production Top-5 (P(+3%) x volatility, walk-forward OOS) — identical to
backtest_exit / the live card. Only the EXIT changes. Intraday is fetched ONLY for the
~Top-5/day picked symbols (not the whole universe), so this stays light.

Data: yfinance 5m history reaches ~58 trading days, so keep K_DAYS <= ~45 to be safe.

Run inside the backend container:
    docker compose exec -T backend python backtest_trail.py [K_DAYS] [TOP_N] [MODEL]
        K_DAYS=30  TOP_N=5  MODEL=xgb
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
    PICK_MAX_RSI, PICK_EXCLUDE_SUPERTREND_DOWN, PICK_VOL_BLEND_WEIGHT,
)
from app.models.models import Stock, PriceDaily
from backtest_p3 import (
    THRESHOLDS, RANK_TH, MIN_TRAIN_ROWS,
    liquid_symbols, cc_targets, train_window, proba_array, _pct_rank_arr,
)

K_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 5
MODEL = sys.argv[3] if len(sys.argv) > 3 else "xgb"
W = PICK_VOL_BLEND_WEIGHT

# The live trailing-stop card serves this artifact (real 5-min-path numbers for the
# OOS-validated trail-2% / arm-+2% rule, plus the hold-to-close baseline for the
# head-to-head). Written by this script; read by screener.trail_scorecard().
TRAIL_PCT = 0.02
ARM_PCT = 0.02
SCORECARD_TRAIL_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "models", "strategy_scorecard_trail_30d.json")


def _select_top(recs, top_n):
    if len(recs) < top_n:
        return None
    p3 = _pct_rank_arr([r["p3"] for r in recs])
    pa = _pct_rank_arr([r["atr_ratio"] for r in recs])
    pr = _pct_rank_arr([r["range_pct"] for r in recs])
    blend = (1 - W) * p3 + W * ((pa + pr) / 2.0)
    order = np.argsort(-blend)[:top_n]
    return [recs[j] for j in order]


# ---------------------------------------------------------------------------
# Exit-rule simulators on an ordered list of intraday bars [(o,h,l,c), ...].
# Each returns the realized return FRACTION vs `entry`. Conservative within a bar:
# the stop level for a bar is taken from the peak/state established by PRIOR bars,
# then the new bar's high updates the peak (no intra-bar look-ahead on the ratchet).
# A gap THROUGH the stop fills at the bar open (worse of open vs stop).
# ---------------------------------------------------------------------------
def sim_hold(entry, bars):
    return (bars[-1][3] - entry) / entry


def sim_target(entry, bars, T):
    tgt = entry * (1 + T)
    for o, h, l, c in bars:
        if h >= tgt:
            return T
    return (bars[-1][3] - entry) / entry


def sim_hardstop(entry, bars, S):
    stop = entry * (1 - S)
    for o, h, l, c in bars:
        if l <= stop:
            fill = min(o, stop)
            return (fill - entry) / entry
    return (bars[-1][3] - entry) / entry


def sim_breakeven(entry, bars, arm_at, floor):
    """Once a bar's high touches +arm_at, set a stop at +floor (0 = breakeven)."""
    armed = False
    floor_p = entry * (1 + floor)
    for o, h, l, c in bars:
        if armed and l <= floor_p:
            fill = min(o, floor_p)
            return (fill - entry) / entry
        if not armed and h >= entry * (1 + arm_at):
            armed = True
            if l <= floor_p:                      # same bar dipped after arming
                fill = min(o, floor_p)
                return (fill - entry) / entry
    return (bars[-1][3] - entry) / entry


def sim_trail(entry, bars, trail, arm_at=0.0):
    """Chandelier trail: after the high reaches +arm_at, stop = running_peak*(1-trail)."""
    return sim_trail_detail(entry, bars, trail, arm_at)[0]


def sim_trail_detail(entry, bars, trail, arm_at=0.0):
    """As sim_trail but also returns the exit reason ('trail' | 'close') and peak %."""
    armed = arm_at <= 0
    peak = entry
    for o, h, l, c in bars:
        if armed:
            stop = peak * (1 - trail)
            if l <= stop:
                fill = min(o, stop)
                return (fill - entry) / entry, "trail", (peak - entry) / entry
        if not armed and h >= entry * (1 + arm_at):
            armed = True
        if armed:
            peak = max(peak, h)
    return (bars[-1][3] - entry) / entry, "close", (peak - entry) / entry


def evaluate(picks_by_day, universe_cc, fn, label):
    daily, allp, hit2, tot, wins = [], [], 0, 0, 0
    bench = []
    for d in sorted(picks_by_day.keys()):
        rets = []
        for p in picks_by_day[d]:
            bars = p.get("bars")
            if not bars:
                continue
            rets.append(fn(p["entry"], bars))
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
    return {"label": label, "npicks": tot, "avg": np.mean(allp) * 100,
            "win": wins / tot * 100, "hit2": hit2 / tot * 100,
            "cum": cum * 100, "edge": (np.mean(daily) - np.mean(bench)) * 100}


def _print(m):
    if not m:
        print("  (no data)")
        return
    print(f"  {m['label']:<42}{m['avg']:>+7.3f}%/pk {m['win']:>4.0f}%win "
          f"{m['hit2']:>4.0f}%≥2% {m['cum']:>+8.2f}%cum (edge {m['edge']:>+.3f}%/d, n={m['npicks']})")


def fetch_intraday(symbols):
    """symbol -> ({date -> [(o,h,l,c), ...] ordered by time}, {date -> 15:20 IST price}).

    Returns two maps: the ordered 5-min bars (for the path-dependent exit rules) and the
    realized ~3:20 PM IST price (close of the last bar starting before 15:20) used by the
    "exit at 3:20 PM" rule. Times are normalised to Asia/Kolkata.
    """
    import yfinance as yf
    cutoff = datetime.time(15, 20)
    bars_out, px320_out = {}, {}
    for k, sym in enumerate(symbols, 1):
        if k % 25 == 0:
            print(f"  ...intraday {k}/{len(symbols)}", flush=True)
        try:
            df = yf.download(f"{sym}.NS", period="60d", interval="5m",
                             progress=False, auto_adjust=False)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close"]].dropna()
        byday, px320 = {}, {}
        for ts, row in df.iterrows():
            tloc = ts.tz_convert("Asia/Kolkata") if ts.tzinfo is not None else ts
            d = tloc.date()
            byday.setdefault(d, []).append(
                (float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])))
            if tloc.time() < cutoff:
                px320[d] = float(row["Close"])   # close of last bar starting before 15:20
        # fall back to the day's final close where no pre-15:20 bar exists
        for d, bars in byday.items():
            px320.setdefault(d, bars[-1][3])
        bars_out[sym] = byday
        px320_out[sym] = px320
    return bars_out, px320_out


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)
    symbols = liquid_symbols(db)
    sym_to_id = {s.symbol: s.id for s in
                 db.query(Stock.symbol, Stock.id).filter(Stock.symbol.in_(symbols)).all()}
    sym_to_name = {s.symbol: (s.company_name or s.symbol) for s in
                   db.query(Stock.symbol, Stock.company_name)
                   .filter(Stock.symbol.in_(symbols)).all()}
    print(f"REAL trailing-stop backtest | universe={len(symbols)} | {K_DAYS}d OOS | "
          f"model={MODEL} | TOP_N={TOP_N}\n", flush=True)

    # Global trading calendar -> next session for each pick date.
    def _as_date(x):
        return x.date() if isinstance(x, datetime.datetime) else x
    all_dates = [_as_date(r[0]) for r in db.query(PriceDaily.timestamp)
                 .distinct().order_by(PriceDaily.timestamp).all()]
    next_of = {all_dates[i]: all_dates[i + 1] for i in range(len(all_dates) - 1)}

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

        def cv(col):
            return test[col].values if col in test.columns else np.full(len(test), np.nan)
        rsi = cv("rsi"); st = cv("supertrend_dir"); atr = cv("atr_ratio"); rng = cv("range_pct")
        idx = test.index
        for i in range(len(test)):
            r = cc[i]
            if r is None or np.isnan(r):
                continue
            universe_cc.setdefault(_as_date(idx[i]), []).append(float(r))
            rsi_i = float(rsi[i]) if not np.isnan(rsi[i]) else None
            st_i = int(st[i]) if not np.isnan(st[i]) else None
            if PICK_EXCLUDE_SUPERTREND_DOWN and st_i == -1:
                continue
            if rsi_i is not None and rsi_i >= PICK_MAX_RSI:
                continue
            row_raw = {th: float(raw[th][i]) for th in THRESHOLDS}
            adj = engine._monotonic_with_base_rates(row_raw, base_rates)
            by_date.setdefault(_as_date(idx[i]), []).append({
                "sym": sym, "p3": adj[RANK_TH], "p2": adj[0.02],
                "atr_ratio": float(atr[i]) if not np.isnan(atr[i]) else np.nan,
                "range_pct": float(rng[i]) if not np.isnan(rng[i]) else np.nan,
                "entry": float(entry[i]) if not np.isnan(entry[i]) else None,
                "pick_date": _as_date(idx[i]),
            })
        processed += 1

    print(f"\nProcessed {processed} stocks ({skipped} skipped).", flush=True)
    dates = sorted(by_date.keys())[-K_DAYS:]

    # Build Top-5 per day (production parity) and attach the holding date.
    picks_by_day = {}
    needed = set()
    for d in dates:
        sel = _select_top(by_date[d], TOP_N)
        if not sel:
            continue
        hold = next_of.get(d)
        if hold is None:
            continue
        for p in sel:
            p["hold_date"] = hold
            if p["entry"]:
                needed.add(p["sym"])
        picks_by_day[d] = sel

    print(f"Fetching 5-min intraday for {len(needed)} picked symbols...", flush=True)
    intraday, intraday_320 = fetch_intraday(sorted(needed))

    # Attach the holding-session intraday bars + 3:20 PM price to each pick.
    matched = missing = 0
    for d, picks in picks_by_day.items():
        for p in picks:
            bars = intraday.get(p["sym"], {}).get(p["hold_date"])
            if bars and p["entry"]:
                p["bars"] = bars
                p["px320"] = intraday_320.get(p["sym"], {}).get(p["hold_date"])
                matched += 1
            else:
                missing += 1
    print(f"Intraday matched {matched} picks ({missing} missing bars -> excluded).\n", flush=True)

    print("=" * 84)
    print("REAL intraday exit rules (5-min path; exact, no best/worst bounds)")
    print("=" * 84)
    base = evaluate(picks_by_day, universe_cc, sim_hold, "HOLD-TO-CLOSE (baseline)")
    _print(base)
    bcum = base["cum"] if base else 0.0
    print()
    print("  --- hard disaster stop (real path) ---")
    for S in (0.05, 0.06):
        _print(evaluate(picks_by_day, universe_cc, lambda e, b, S=S: sim_hardstop(e, b, S),
                        f"HARD STOP -{S*100:.0f}%"))
    print()
    print("  --- breakeven-after-touch (the daily-bar-ambiguous rule, now EXACT) ---")
    for A, fl in ((0.02, 0.0), (0.02, 0.005), (0.015, 0.0), (0.025, 0.01)):
        _print(evaluate(picks_by_day, universe_cc,
                        lambda e, b, A=A, fl=fl: sim_breakeven(e, b, A, fl),
                        f"touch +{A*100:.1f}% -> floor +{fl*100:.1f}%"))
    print()
    print("  --- chandelier trailing stop (real path) ---")
    for tr, arm in ((0.02, 0.0), (0.03, 0.0), (0.02, 0.02), (0.03, 0.02), (0.015, 0.015)):
        _print(evaluate(picks_by_day, universe_cc,
                        lambda e, b, tr=tr, arm=arm: sim_trail(e, b, tr, arm),
                        f"trail {tr*100:.1f}% (arm +{arm*100:.1f}%)"))
    print()
    print("  --- fixed profit target (real path, for reference) ---")
    for T in (0.02, 0.03):
        _print(evaluate(picks_by_day, universe_cc, lambda e, b, T=T: sim_target(e, b, T),
                        f"TARGET +{T*100:.0f}%"))
    print()
    print(f"Baseline hold-to-close = {bcum:+.2f}% cum. Ship a rule only if it BEATS this")
    print("with comparable coverage. These are EXACT 5-min-path numbers (no bounds).")

    write_artifact(picks_by_day, sym_to_name)


def _overall(rets, daily):
    if not rets:
        return None
    tot = len(rets)
    green = sum(1 for v in rets if v > 0.0001)
    hit2 = sum(1 for v in rets if v >= 0.02)
    hit3 = sum(1 for v in rets if v >= 0.03)
    cum = float(np.prod([1 + m for m in daily]) - 1)
    return {
        "scored": tot,
        "avg_cc": round(float(np.mean(rets)) * 100, 2),
        "green": green,
        "win_rate": round(green / tot * 100, 1),
        "hit2": hit2,
        "hit2_rate": round(hit2 / tot * 100, 1),
        "hit3": hit3,
        "hit3_rate": round(hit3 / tot * 100, 1),
        "cum_pct": round(cum * 100, 2),
    }


def write_artifact(picks_by_day, sym_to_name):
    """Write the multi-exit scorecard the sibling cards serve, all from real 5-min paths:
    hold-to-close (baseline), 2% trailing (armed +2%), -6% disaster stop, and 3:20 PM exit."""
    days_out = []
    trail_all, base_all, exit320_all, stop_all = [], [], [], []
    trail_daily, base_daily, exit320_daily, stop_daily = [], [], [], []
    for d in sorted(picks_by_day.keys()):
        picks_out = []
        t_day, b_day, e_day, s_day = [], [], [], []
        for p in picks_by_day[d]:
            bars = p.get("bars")
            if not bars or not p.get("entry"):
                continue
            entry = p["entry"]
            t_cc, reason, peak = sim_trail_detail(entry, bars, TRAIL_PCT, ARM_PCT)
            b_cc = sim_hold(entry, bars)
            s_cc = sim_hardstop(entry, bars, 0.06)
            px320 = p.get("px320")
            e_cc = (px320 - entry) / entry if px320 else b_cc
            t_day.append(t_cc); b_day.append(b_cc); e_day.append(e_cc); s_day.append(s_cc)
            picks_out.append({
                "symbol": p["sym"],
                "company_name": sym_to_name.get(p["sym"], p["sym"]),
                "entry": round(entry, 2),
                "prob_plus_3": round(p.get("p3", 0) * 100, 1),
                "prob_plus_2": round(p.get("p2", 0) * 100, 1),
                "peak_pct": round(peak * 100, 2),
                "cc": round(t_cc * 100, 2),
                "base_cc": round(b_cc * 100, 2),
                "exit320_cc": round(e_cc * 100, 2),
                "stop_cc": round(s_cc * 100, 2),
                "px320": round(px320, 2) if px320 else None,
                "exit_reason": reason,
                "outcome": "WIN" if t_cc > 0.0001 else ("LOSS" if t_cc < -0.0001 else "FLAT"),
            })
        if not picks_out:
            continue
        trail_all.extend(t_day); base_all.extend(b_day)
        exit320_all.extend(e_day); stop_all.extend(s_day)
        trail_daily.append(float(np.mean(t_day))); base_daily.append(float(np.mean(b_day)))
        exit320_daily.append(float(np.mean(e_day))); stop_daily.append(float(np.mean(s_day)))
        days_out.append({
            "pick_date": d.isoformat(),
            "result_date": picks_by_day[d][0].get("hold_date").isoformat()
            if picks_by_day[d][0].get("hold_date") else None,
            "pending": False,
            "picks": picks_out,
            "summary": {
                "scored": len(picks_out),
                "avg_cc": round(float(np.mean(t_day)) * 100, 2),
                "green": sum(1 for v in t_day if v > 0),
                "hit2": sum(1 for v in t_day if v >= 0.02),
                "hit3": sum(1 for v in t_day if v >= 0.03),
            },
        })
    art = {
        "top_n": TOP_N,
        "rule": "trail2_arm2",
        "trail_pct": TRAIL_PCT,
        "arm_pct": ARM_PCT,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "days": list(reversed(days_out)),
        "overall": _overall(trail_all, trail_daily),
        "baseline_overall": _overall(base_all, base_daily),
        "exit320_overall": _overall(exit320_all, exit320_daily),
        "stop_overall": _overall(stop_all, stop_daily),
    }
    os.makedirs(os.path.dirname(SCORECARD_TRAIL_JSON), exist_ok=True)
    with open(SCORECARD_TRAIL_JSON, "w") as f:
        json.dump(art, f, indent=2)
    print(f"\nWrote scorecard artifact -> {SCORECARD_TRAIL_JSON} ({len(days_out)} days)"
          f"\n  hold  ={art['baseline_overall']}"
          f"\n  trail ={art['overall']}"
          f"\n  3:20  ={art['exit320_overall']}"
          f"\n  -6%   ={art['stop_overall']}")


if __name__ == "__main__":
    main()
