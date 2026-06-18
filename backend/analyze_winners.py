"""
WHY do stocks hit +2% next day?  A direct, model-free conditional-lift study.

The question: across the quality universe, which (stock, day) setups actually produced a
next-day close-to-close move >= +2%, and what feature values did those winners share?

Method (no ML, no look-ahead): for every stock and every historical day t in the lookback,
take the 33 production features known at t's close and the realized next-day outcome
(next_close - close)/close. Pool everything, then for each feature compare the WINNERS
(next day >= +2%) against the FIELD:

  * base_rate          = P(next day >= +2%) across all setups
  * decile lift        = split the feature into 10 buckets; show P(+2%) in the top vs bottom
                         bucket -> tells you if HIGH or LOW values of that feature help, and
                         how much edge there is.
  * winner vs field    = mean feature value among winners vs everyone (effect direction).

Then it builds a simple, transparent RULE SET from the most predictive features and reports
the precision/lift/coverage of stacking them — i.e. "if you only take setups where these
N conditions hold, your +2% hit-rate goes from base% to X%."

Run inside the backend container:
    docker compose exec -T backend python analyze_winners.py [LOOKBACK_DAYS]
"""
import sys
import datetime
import numpy as np
import pandas as pd

from app.database import SessionLocal
from app.services.prediction import PredictionEngine
from app.services.screener import (
    MIN_PICK_PRICE, MIN_PICK_TURNOVER, PICK_LIQUIDITY_WINDOW_DAYS,
)
from app.models.models import PriceDaily, Stock
from sqlalchemy import func, text

LOOKBACK = int(sys.argv[1]) if len(sys.argv) > 1 else 180
TARGET = 0.02  # +2% next-day close-to-close

FEATURES = [
    'rsi', 'macd_hist', 'volume_ratio', 'dist_ema_20',
    'dist_ema_50', 'dist_ema_200', 'atr_ratio', 'adx',
    'return_1d', 'return_3d', 'return_5d',
    'stoch_k', 'mfi', 'cci', 'williams_r', 'supertrend_dir',
    'roc_10', 'roc_20', 'realized_vol_20',
    'dist_52w_high', 'dist_52w_low', 'rs_nifty_20', 'beta_60',
    'bb_pct', 'gap_open', 'vol_zscore_20', 'range_pct',
    'atr_expansion', 'close_pos_range', 'oh_today',
    'oh_mean_20', 'oh_freq2_20', 'dist_prior_high5',
]


def liquid_symbols(db):
    latest = db.query(func.max(PriceDaily.timestamp)).scalar()
    cutoff = latest - datetime.timedelta(days=PICK_LIQUIDITY_WINDOW_DAYS)
    # NSE: no market-cap/shares data exists, so average daily turnover (close*volume) is the
    # size proxy. Take the TOP 500 most-liquid names -> reproduces the Nifty-500 large/mid-cap
    # universe (every name here is far above a Rs.1000 Cr market cap).
    rows = db.execute(text("""
        WITH turnover AS (
            SELECT stock_id, AVG(close*volume) AS avg_turnover
            FROM prices_daily WHERE timestamp >= :cutoff GROUP BY stock_id
        ), lastclose AS (
            SELECT DISTINCT ON (stock_id) stock_id, close
            FROM prices_daily ORDER BY stock_id, timestamp DESC
        )
        SELECT s.symbol
        FROM stocks s JOIN turnover t ON t.stock_id=s.id JOIN lastclose lc ON lc.stock_id=s.id
        WHERE s.is_active=TRUE AND lc.close>=:minprice AND t.avg_turnover>=:minturn
        ORDER BY t.avg_turnover DESC
        LIMIT 500
    """), {"cutoff": cutoff, "minprice": MIN_PICK_PRICE, "minturn": MIN_PICK_TURNOVER})
    return [r[0] for r in rows]


def next_cc(db, stock_id, index):
    rows = db.query(PriceDaily.timestamp, PriceDaily.close)\
        .filter(PriceDaily.stock_id == stock_id).order_by(PriceDaily.timestamp).all()
    if not rows:
        return None
    df = pd.DataFrame([{"date": r[0], "close": float(r[1])} for r in rows]).set_index("date")
    df["cc"] = df["close"].shift(-1) / df["close"] - 1.0
    return df["cc"].reindex(index)


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)
    symbols = liquid_symbols(db)
    sym_to_id = {s.symbol: s.id for s in
                 db.query(Stock.symbol, Stock.id).filter(Stock.symbol.in_(symbols)).all()}
    print(f"[WHY +2%?] universe={len(symbols)} | last {LOOKBACK} sessions per stock\n", flush=True)

    frames = []
    used = 0
    for n_done, sym in enumerate(symbols, 1):
        if n_done % 100 == 0:
            print(f"  ...scanned {n_done}/{len(symbols)} (used {used})", flush=True)
        X, _y = engine._prepare_data(sym)
        if X is None or len(X) < 60:
            continue
        cc = next_cc(db, sym_to_id[sym], X.index)
        if cc is None:
            continue
        sub = X.iloc[-(LOOKBACK + 1):-1].copy()       # drop last row (no next day)
        sub["cc"] = cc.reindex(sub.index)
        sub = sub.dropna(subset=["cc"])
        if len(sub):
            frames.append(sub[FEATURES + ["cc"]])
            used += 1

    if not frames:
        print("no data"); db.close(); return
    df = pd.concat(frames, ignore_index=True)
    df["win"] = (df["cc"] >= TARGET).astype(int)
    n = len(df)
    base = df["win"].mean()
    print(f"\nPooled setups: {n:,} | base P(next-day >= +2%) = {base*100:.2f}%")
    print(f"(winners = {int(df['win'].sum()):,})\n")

    # ---- univariate decile lift ----
    print("===== FEATURE LIFT (top-decile vs bottom-decile P(+2%)) =====")
    print(f"{'feature':<18}{'lowDec%':>9}{'topDec%':>9}{'lift(top/base)':>16}{'winMean':>11}{'fieldMean':>11}")
    rows = []
    for f in FEATURES:
        col = df[f]
        if col.nunique() < 10:
            try:
                q_low, q_hi = col.quantile(0.1), col.quantile(0.9)
            except Exception:
                continue
        else:
            q_low, q_hi = col.quantile(0.1), col.quantile(0.9)
        low_mask = col <= q_low
        hi_mask = col >= q_hi
        if low_mask.sum() < 50 or hi_mask.sum() < 50:
            continue
        p_low = df.loc[low_mask, "win"].mean()
        p_hi = df.loc[hi_mask, "win"].mean()
        win_mean = df.loc[df["win"] == 1, f].mean()
        field_mean = col.mean()
        lift = p_hi / base if base > 0 else 0
        rows.append((f, p_low, p_hi, lift, win_mean, field_mean))
    # sort by absolute edge between top and bottom decile
    rows.sort(key=lambda r: abs(r[2] - r[1]), reverse=True)
    for f, p_low, p_hi, lift, wm, fm in rows:
        print(f"{f:<18}{p_low*100:>8.1f}{p_hi*100:>9.1f}{lift:>15.2f}x{wm:>11.3f}{fm:>11.3f}")

    # ---- build a transparent stacked rule set from the strongest signals ----
    print("\n===== STACKED RULE SET (transparent, data-driven) =====")
    conds = {
        "uptrend(close>EMA200)": df["dist_ema_200"] > 0,
        "EMA20>EMA50":          df["dist_ema_20"] < df["dist_ema_50"],
        "supertrend up":        df["supertrend_dir"] == 1,
        "ADX>=18 (trending)":   df["adx"] >= 18,
        "vol_ratio>=1.1":       df["volume_ratio"] >= 1.1,
        "RSI 50-68 (not hot)":  (df["rsi"] >= 50) & (df["rsi"] <= 68),
        "5d momentum>0":        df["return_5d"] > 0,
        "not stretched(<8% EMA20)": df["dist_ema_20"] < 0.08,
    }
    print(f"{'condition':<28}{'n':>9}{'P(+2%)':>9}{'lift':>8}")
    for name, m in conds.items():
        sub = df.loc[m, "win"]
        if len(sub) < 50:
            continue
        p = sub.mean()
        print(f"{name:<28}{len(sub):>9,}{p*100:>8.1f}%{(p/base):>7.2f}x")

    # stack them progressively
    print("\n--- stacking conditions (precision vs coverage) ---")
    order = ["uptrend(close>EMA200)", "supertrend up", "EMA20>EMA50", "ADX>=18 (trending)",
             "vol_ratio>=1.1", "5d momentum>0", "RSI 50-68 (not hot)", "not stretched(<8% EMA20)"]
    mask = pd.Series(True, index=df.index)
    print(f"{'+ condition':<30}{'n setups':>10}{'P(+2%)':>9}{'lift':>8}")
    for name in order:
        mask = mask & conds[name]
        sub = df.loc[mask, "win"]
        if len(sub) < 30:
            print(f"{'+ ' + name:<30}{len(sub):>10,}  (too few, stop)")
            break
        p = sub.mean()
        print(f"{'+ ' + name:<30}{len(sub):>10,}{p*100:>8.1f}%{(p/base):>7.2f}x")

    db.close()


if __name__ == "__main__":
    main()
