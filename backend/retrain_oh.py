"""
Retrain the universe after the Open->High model change.

The feature set (now 33 cols) and training target (now next-day open->high) changed,
so every previously-saved {symbol}_*_models.pkl is incompatible and must be rebuilt.

For each active stock with >= 100 candles:
  * train_models(sym)      -> writes a fresh ensemble model file with the new features/target
  * predict_next_day(sym)  -> persists a fresh Prediction row the screener ranks on

Run inside the backend container:
    docker compose exec -T backend python retrain_oh.py
"""
import glob
import os
import time

from app.database import SessionLocal
from app.models.models import Stock, PriceDaily
from app.services.prediction import PredictionEngine, MODEL_DIR
from app.utils.job_progress import start_job, set_progress, finish_job, fail_job
from sqlalchemy import func


def main():
    db = SessionLocal()
    engine = PredictionEngine(db)

    old = glob.glob(os.path.join(MODEL_DIR, "*_models.pkl"))
    for p in old:
        try:
            os.remove(p)
        except OSError:
            pass
    print(f"Removed {len(old)} stale model files.", flush=True)

    counts = dict(
        db.query(PriceDaily.stock_id, func.count())
        .group_by(PriceDaily.stock_id).all()
    )
    stocks = db.query(Stock).filter(Stock.is_active == True).order_by(Stock.symbol).all()
    total = len(stocks)
    print(f"Active stocks: {total}", flush=True)

    start_job("retrain", total=total, message=f"Retraining models for {total} stocks…")

    ok = trained = failed = 0
    t0 = time.time()
    for i, st in enumerate(stocks, 1):
        if counts.get(st.id, 0) < 100:
            continue
        try:
            if engine.train_models(st.symbol):
                trained += 1
                engine.predict_next_day(st.symbol)
                ok += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            if failed <= 10:
                print(f"  FAIL {st.symbol}: {str(e)[:100]}", flush=True)
        if i % 100 == 0:
            print(f"  {i}/{total}  trained={trained} predicted={ok} failed={failed} "
                  f"({time.time()-t0:.0f}s)", flush=True)
            set_progress(current=i, message=f"Retraining… {i}/{total} stocks ({trained} done, {failed} failed)")

    finish_job(message=f"Retrain complete: {trained} models rebuilt, {ok} predictions updated")
    print(f"\nDONE: trained={trained} predicted={ok} failed={failed} "
          f"in {time.time()-t0:.0f}s", flush=True)
    db.close()


if __name__ == "__main__":
    main()
