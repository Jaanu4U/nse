"""
Multi-day backtest for the Top-Picks ranking logic.

For each of the last K trading dates it reconstructs the ML composite (from the saved
per-stock models) + trend/volatility adjusted score for every liquid stock, ranks the
top-N, and measures the realised next-day return. This lets us compare ranking variants
(raw ML vs trend/vol-adjusted) against each other and against the market baseline across
many days instead of trusting a single noisy session.

Run inside the backend container:
    docker compose exec -T backend python backtest_picks.py [K] [N]
"""
import os
import sys
import pickle
import datetime
import statistics
import numpy as np

from app.database import SessionLocal
from app.services.prediction import PredictionEngine, MODEL_DIR
from app.services.screener import (
    pick_final_score, MIN_PICK_PRICE, MIN_PICK_TURNOVER, PICK_LIQUIDITY_WINDOW_DAYS,
)
from app.models.models import PriceDaily
from sqlalchemy import func, text

K_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 40
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 25


def liquid_symbols(db):
    """Symbols passing the same price + turnover floor used by get_top_picks."""
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
        WHERE s.is_active = TRUE
          AND lc.close >= :minprice
          AND t.avg_turnover >= :minturn
    """), {"cutoff": cutoff, "minprice": MIN_PICK_PRICE, "minturn": MIN_PICK_TURNOVER})
    return [r[0] for r in rows]


def composite_from_models(models, X):
    """Vectorised ML composite (0.40/0.30/0.20/0.10 weighting) for every row of X."""
    weights = {0.01: 0.40, 0.02: 0.30, 0.03: 0.20, 0.05: 0.10}
    comp = np.zeros(len(X))
    for th, w in weights.items():
        m = models.get(th)
        if m is None:
            continue
        if isinstance(m, float):
            comp += w * m
        else:
            comp += w * m.predict_proba(X)[:, 1]
    return comp


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)
    symbols = liquid_symbols(db)
    print(f"Liquid universe: {len(symbols)} stocks | testing last {K_DAYS} trading days, top {TOP_N}")

    # date -> list of (symbol, final_score, ml_score, next_day_return)
    by_date = {}
    processed = 0
    for sym in symbols:
        model_path = os.path.join(MODEL_DIR, f"{sym}_models.pkl")
        if not os.path.exists(model_path):
            continue
        X, y = engine._prepare_data(sym)
        if X is None or len(X) < 30:
            continue
        try:
            with open(model_path, "rb") as f:
                models = pickle.load(f)
            comp = composite_from_models(models, X)
        except Exception:
            continue

        nd = y["next_day_return"].values
        idx = X.index
        d20 = X["dist_ema_20"].values
        d50 = X["dist_ema_50"].values
        d200 = X["dist_ema_200"].values
        st = X["supertrend_dir"].values
        adx = X["adx"].values
        rsi = X["rsi"].values
        atrr = X["atr_ratio"].values

        # Only keep the most recent K dates that have a realised next-day return.
        for i in range(max(0, len(X) - K_DAYS - 1), len(X)):
            r = nd[i]
            if r is None or (isinstance(r, float) and np.isnan(r)):
                continue
            ml = float(comp[i])
            final = pick_final_score(
                ml, 0.0,
                close_gt_ema50=bool(d50[i] > 0),
                ema20_gt_ema50=bool(d20[i] < d50[i]),
                close_gt_ema200=bool(d200[i] > 0),
                supertrend_dir=int(st[i]) if not np.isnan(st[i]) else None,
                adx=float(adx[i]) if not np.isnan(adx[i]) else None,
                rsi=float(rsi[i]) if not np.isnan(rsi[i]) else None,
                atr_ratio=float(atrr[i]) if not np.isnan(atrr[i]) else None,
            )
            by_date.setdefault(idx[i], []).append((sym, final, ml, float(r)))
        processed += 1

    print(f"Processed {processed} stocks with trained models.\n")

    dates = sorted(by_date.keys())[-K_DAYS:]

    def evaluate(rank_key_index):
        """rank_key_index: 1=final_score, 2=ml_score. Returns aggregate stats."""
        day_rets, day_wins, top10_rets, market_rets = [], [], [], []
        for d in dates:
            recs = by_date[d]
            if len(recs) < TOP_N:
                continue
            ranked = sorted(recs, key=lambda t: t[rank_key_index], reverse=True)
            top = ranked[:TOP_N]
            rets = [t[3] for t in top]
            day_rets.append(statistics.mean(rets))
            day_wins.append(sum(1 for x in rets if x > 0) / len(rets))
            top10_rets.append(statistics.mean([t[3] for t in ranked[:10]]))
            market_rets.append(statistics.mean([t[3] for t in recs]))
        return day_rets, day_wins, top10_rets, market_rets

    for label, key in [("TREND+VOL ADJUSTED (production)", 1), ("RAW ML ONLY (old logic)", 2)]:
        dr, dw, t10, mk = evaluate(key)
        if not dr:
            print(f"{label}: not enough data")
            continue
        print(f"== {label} ==")
        print(f"  days evaluated      : {len(dr)}")
        print(f"  avg next-day return : {statistics.mean(dr)*100:+.3f}%  (top {TOP_N})")
        print(f"  avg top-10 return   : {statistics.mean(t10)*100:+.3f}%")
        print(f"  win rate            : {statistics.mean(dw)*100:.1f}%")
        print(f"  market baseline     : {statistics.mean(mk)*100:+.3f}%  (all liquid)")
        print(f"  edge vs market      : {(statistics.mean(dr)-statistics.mean(mk))*100:+.3f}%")
        print(f"  % up-days           : {sum(1 for x in dr if x>0)/len(dr)*100:.1f}%")
        print()

    db.close()


if __name__ == "__main__":
    main()
