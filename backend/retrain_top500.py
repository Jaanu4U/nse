"""
Fast retrain: rebuild models only for the top-500 quality stocks (ranked by avg
30-day turnover). These are the only names the screener selects from, so retraining
the rest of the 2126-stock universe is unnecessary for production picks.

Run inside the backend container:
    docker compose exec -T backend python retrain_top500.py
"""
import glob
import os
import time
import datetime

from app.database import SessionLocal
from app.models.models import Stock, PriceDaily
from app.services.prediction import PredictionEngine, MODEL_DIR
from app.services.screener import MIN_PICK_PRICE, MIN_PICK_TURNOVER, PICK_LIQUIDITY_WINDOW_DAYS
from app.utils.job_progress import start_job, set_progress, finish_job, fail_job
from sqlalchemy import func, text


def quality_symbols(db, limit=500):
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
        SELECT s.symbol, s.id
        FROM stocks s
        JOIN turnover t ON t.stock_id = s.id
        JOIN lastclose lc ON lc.stock_id = s.id
        WHERE s.is_active = TRUE AND lc.close >= :minprice AND t.avg_turnover >= :minturn
        ORDER BY t.avg_turnover DESC
        LIMIT :lim
    """), {"cutoff": cutoff, "minprice": MIN_PICK_PRICE, "minturn": MIN_PICK_TURNOVER, "lim": limit})
    return [r[0] for r in rows]


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)

    symbols = quality_symbols(db, limit=500)
    total = len(symbols)
    print(f"Quality top-{total} universe selected.", flush=True)

    # Remove stale pkl files only for these symbols so we don't clobber
    # models for other stocks unnecessarily.
    removed = 0
    for sym in symbols:
        for pat in [f"{sym}_ens_models.pkl", f"{sym}_xgb_models.pkl", f"{sym}_lgbm_models.pkl"]:
            p = os.path.join(MODEL_DIR, pat)
            if os.path.exists(p):
                os.remove(p)
                removed += 1
    print(f"Removed {removed} stale model files for top-{total} stocks.", flush=True)

    start_job("retrain", total=total, message=f"Retraining top-{total} quality stocks…")

    counts = dict(
        db.query(PriceDaily.stock_id, func.count())
        .group_by(PriceDaily.stock_id)
        .filter(PriceDaily.stock_id.in_(
            db.query(Stock.id).filter(Stock.symbol.in_(symbols))
        )).all()
    )
    sym_to_id = {s.symbol: s.id for s in
                 db.query(Stock.symbol, Stock.id).filter(Stock.symbol.in_(symbols)).all()}

    ok = trained = failed = 0
    t0 = time.time()
    for i, sym in enumerate(symbols, 1):
        if counts.get(sym_to_id.get(sym), 0) < 100:
            continue
        try:
            if engine.train_models(sym):
                trained += 1
                engine.predict_next_day(sym)
                ok += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"  FAIL {sym}: {str(e)[:80]}", flush=True)
        if i % 50 == 0:
            pct = round(i / total * 100, 1)
            print(f"  {i}/{total}  trained={trained} predicted={ok} failed={failed} "
                  f"({time.time()-t0:.0f}s)", flush=True)
            set_progress(current=i, message=f"Retraining… {i}/{total} ({pct}%) — {trained} done")

    finish_job(message=f"Retrain complete: {trained} models rebuilt, {ok} predictions updated")
    print(f"\nDONE: trained={trained} predicted={ok} failed={failed} "
          f"in {time.time()-t0:.0f}s", flush=True)
    db.close()


if __name__ == "__main__":
    main()
