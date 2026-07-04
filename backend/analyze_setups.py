"""
WHY did THIS stock pop +2% on THIS day?  — per-event setup forensics.

Unlike analyze_winners.py (which pools all setups and does univariate decile lift),
this tool TAGS every (stock, day) setup with named trading-setup ARCHETYPES built from
the production features, then answers three questions over the last LOOKBACK sessions of
the quality universe:

  1) ANATOMY OF A WINNER  — of all the days a stock actually moved >= +2% next session,
     what setup were they sitting in?  (% of winners carrying each tag)

  2) DOES THE SETUP PREDICT IT  — precision P(+2% | tag) and lift vs the base rate, plus
     the best single tag and the best 2-tag COMBINATION (min coverage) you could trade.

  3) WINNER vs LOSER, SAME SETUP  — within each archetype, the feature that most separates
     the names that DID pop from the ones that fired the same setup and FIZZLED. This is
     the real edge: not "what do winners look like" but "what splits winners from look-alikes".

Then a DAY-BY-DAY tape: for the most recent sessions it prints that day's biggest movers
with their why-tags and a compact snapshot (volume, ROC, gap, ATR, RSI, dist-to-high) so
you can read why each name ran that specific day.

Archetypes (domain logic, multi-tag — a setup can carry several):
  VOL_EXPANSION   atr expanding + above-median atr        "coiled spring releasing"
  VOLUME_SURGE    volume z-score / ratio spike            "institutional footprint"
  MOMENTUM        positive 10d ROC + 5d return + >EMA20    "trend continuation"
  BREAKOUT        within ~2% of prior-5d / 52w high        "breaking resistance"
  OVERSOLD_BOUNCE low RSI / low %B / deep williams         "snapback / mean reversion"
  GAP_GO          gapped up at the open                    "gap and go"
  HABITUAL_MOVER  high oh_freq2_20                         "naturally volatile, often +2%"
  STRETCHED(weak) far above EMA20 or RSI hot               "overextended — fade risk"

Run inside the backend container:
    docker compose exec -T backend python analyze_setups.py [LOOKBACK_DAYS] [DAYS_TAPE]
        LOOKBACK_DAYS = 60 (default)    DAYS_TAPE = 8 (recent sessions to print name-by-name)
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

LOOKBACK = int(sys.argv[1]) if len(sys.argv) > 1 else 60
DAYS_TAPE = int(sys.argv[2]) if len(sys.argv) > 2 else 8
TARGET = 0.02   # +2% next-day close-to-close


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
        FROM stocks s JOIN turnover t ON t.stock_id=s.id JOIN lastclose lc ON lc.stock_id=s.id
        WHERE s.is_active=TRUE AND lc.close>=:minprice AND t.avg_turnover>=:minturn
        ORDER BY t.avg_turnover DESC LIMIT 500
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


# ---- archetype tagging -------------------------------------------------------
def tag_setups(df, med):
    """Return a dict tag -> boolean Series. `med` = column medians of the pool."""
    g = df.get
    def col(c, default=0.0):
        return df[c] if c in df.columns else pd.Series(default, index=df.index)
    atr = col("atr_ratio"); atr_exp = col("atr_expansion"); rng = col("range_pct")
    volz = col("vol_zscore_20"); volr = col("volume_ratio")
    roc10 = col("roc_10"); ret5 = col("return_5d"); de20 = col("dist_ema_20")
    d5h = col("dist_prior_high5"); d52h = col("dist_52w_high")
    rsi = col("rsi"); bbp = col("bb_pct"); wr = col("williams_r")
    gap = col("gap_open"); ohf = col("oh_freq2_20")
    return {
        "VOL_EXPANSION":   (atr_exp >= 1.10) & (atr >= med["atr_ratio"]),
        "VOLUME_SURGE":    (volz >= 1.0) | (volr >= 1.5),
        "MOMENTUM":        (roc10 > 0) & (ret5 > 0) & (de20 > 0),
        "BREAKOUT":        (d5h >= -0.02) | (d52h >= -0.05),
        "OVERSOLD_BOUNCE": (rsi <= 40) | (bbp <= 0.15) | (wr <= -80),
        "GAP_GO":          gap >= 0.01,
        "HABITUAL_MOVER":  ohf >= 0.45,
        "STRETCHED(weak)": (de20 >= 0.08) | (rsi >= 75),
    }


SNAP = ["volume_ratio", "vol_zscore_20", "roc_10", "roc_20", "gap_open",
        "atr_ratio", "range_pct", "rsi", "dist_prior_high5", "dist_52w_high"]


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)
    symbols = liquid_symbols(db)
    sym_to_id = {s.symbol: s.id for s in
                 db.query(Stock.symbol, Stock.id).filter(Stock.symbol.in_(symbols)).all()}
    print(f"[WHY THIS STOCK, THIS DAY] universe={len(symbols)} | last {LOOKBACK} sessions\n", flush=True)

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
        sub = X.iloc[-(LOOKBACK + 1):-1].copy()
        sub["cc"] = cc.reindex(sub.index)
        sub = sub.dropna(subset=["cc"])
        if len(sub):
            sub = sub.copy()
            sub["symbol"] = sym
            sub["date"] = sub.index
            frames.append(sub)
            used += 1

    if not frames:
        print("no data"); db.close(); return
    df = pd.concat(frames, ignore_index=True)
    df["win"] = (df["cc"] >= TARGET).astype(int)
    base = df["win"].mean()
    n = len(df)
    nwin = int(df["win"].sum())
    med = {c: df[c].median() for c in df.columns if df[c].dtype != object}
    tags = tag_setups(df, med)
    for t, m in tags.items():
        df[t] = m.values
    tagnames = list(tags.keys())

    print(f"\nPooled setups: {n:,} | base P(+2% next day) = {base*100:.2f}%  (winners={nwin:,})\n")

    # ---- 1) ANATOMY OF A WINNER ----
    print("=" * 72)
    print("1) ANATOMY OF A WINNER — of stocks that DID pop +2%, what setup were they in?")
    print("=" * 72)
    win_df = df[df["win"] == 1]
    print(f"  {'archetype':<20}{'% of winners':>14}{'(also % of field)':>20}")
    rows = []
    for t in tagnames:
        share_win = win_df[t].mean() * 100
        share_all = df[t].mean() * 100
        rows.append((t, share_win, share_all))
    for t, sw, sa in sorted(rows, key=lambda r: -r[1]):
        flag = "  <-- over-represented in winners" if sw > sa + 2 else ""
        print(f"  {t:<20}{sw:>13.1f}%{sa:>18.1f}%{flag}")
    nontag = win_df[tagnames].sum(axis=1).eq(0).mean() * 100
    print(f"  (winners carrying NO tag: {nontag:.1f}%  -> 'came out of nowhere')\n")

    # ---- 2) DOES THE SETUP PREDICT IT — precision & lift ----
    print("=" * 72)
    print("2) DOES THE SETUP PREDICT IT — P(+2% | setup) and lift vs base")
    print("=" * 72)
    print(f"  {'archetype':<20}{'n':>8}{'P(+2%)':>9}{'lift':>7}")
    prec = {}
    for t in tagnames:
        sub = df.loc[df[t], "win"]
        if len(sub) < 50:
            continue
        p = sub.mean(); prec[t] = (p, len(sub))
        print(f"  {t:<20}{len(sub):>8,}{p*100:>8.1f}%{p/base:>6.2f}x")

    # best 2-tag combination by precision (min coverage 150)
    print("\n  --- best 2-setup COMBINATIONS (min 150 setups) ---")
    combos = []
    for i in range(len(tagnames)):
        for j in range(i + 1, len(tagnames)):
            a, b = tagnames[i], tagnames[j]
            m = df[a] & df[b]
            ns = int(m.sum())
            if ns < 150:
                continue
            p = df.loc[m, "win"].mean()
            combos.append((a, b, ns, p))
    combos.sort(key=lambda r: -r[3])
    print(f"  {'combo':<38}{'n':>8}{'P(+2%)':>9}{'lift':>7}")
    for a, b, ns, p in combos[:8]:
        print(f"  {a} + {b:<{36-len(a)-3}}{ns:>8,}{p*100:>8.1f}%{p/base:>6.2f}x")

    # ---- 3) WINNER vs LOSER, SAME SETUP ----
    print("\n" + "=" * 72)
    print("3) WINNER vs LOSER, SAME SETUP — what splits the pops from the look-alikes?")
    print("=" * 72)
    discr_cols = ["volume_ratio", "vol_zscore_20", "roc_10", "roc_20", "gap_open",
                  "atr_ratio", "range_pct", "rsi", "dist_prior_high5",
                  "close_pos_range", "macd_hist", "adx"]
    for t in tagnames:
        m = df[t]
        if m.sum() < 150:
            continue
        w = df[m & (df["win"] == 1)]
        l = df[m & (df["win"] == 0)]
        if len(w) < 30 or len(l) < 30:
            continue
        diffs = []
        for c in discr_cols:
            if c not in df.columns:
                continue
            wm, lm = w[c].mean(), l[c].mean()
            sd = df.loc[m, c].std()
            if not sd or np.isnan(sd):
                continue
            diffs.append((c, (wm - lm) / sd, wm, lm))
        diffs.sort(key=lambda r: -abs(r[1]))
        top = diffs[:3]
        desc = ", ".join(f"{c}({z:+.2f}σ w{wm:.2f}/l{lm:.2f})" for c, z, wm, lm in top)
        print(f"  {t:<18} winners differ most by: {desc}")
    print()

    # ---- DAY-BY-DAY TAPE ----
    print("=" * 72)
    print(f"DAY-BY-DAY TAPE — biggest movers of each of the last {DAYS_TAPE} sessions + why-tags")
    print("=" * 72)
    all_dates = sorted(df["date"].unique())[-DAYS_TAPE:]
    for d in all_dates:
        day = df[(df["date"] == d) & (df["win"] == 1)].copy()
        if day.empty:
            continue
        day = day.sort_values("cc", ascending=False).head(8)
        dd = pd.Timestamp(d).date()
        navg = df[df["date"] == d]["win"].mean() * 100
        print(f"\n  {dd}  ({len(df[(df['date']==d)&(df['win']==1)])} names popped +2% / "
              f"{len(df[df['date']==d])} = {navg:.0f}% of universe)")
        for _, r in day.iterrows():
            on = [t for t in tagnames if r[t]]
            tagstr = ",".join(t.replace("(weak)", "") for t in on) or "untagged"
            print(f"    {r['symbol']:<13} +{r['cc']*100:>4.1f}%  "
                  f"vol×{r.get('volume_ratio',0):.1f} z{r.get('vol_zscore_20',0):+.1f} "
                  f"roc10 {r.get('roc_10',0)*100:+.1f}% gap{r.get('gap_open',0)*100:+.1f}% "
                  f"atr{r.get('atr_ratio',0)*100:.1f}% rsi{r.get('rsi',0):.0f} "
                  f"d5h{r.get('dist_prior_high5',0)*100:+.0f}%  [{tagstr}]")

    db.close()


if __name__ == "__main__":
    main()
