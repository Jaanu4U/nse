import datetime, logging, time, sys
logging.basicConfig(level=logging.ERROR)
from app.database import SessionLocal
from app.models.models import Stock, Prediction
from app.services.data_collection import DataCollectionEngine
from app.services.technical_analysis import TechnicalAnalysisEngine
from app.services.pattern_recognition import PatternRecognitionEngine
from app.services.prediction import PredictionEngine

db = SessionLocal()
dc = DataCollectionEngine(db)
ta = TechnicalAnalysisEngine(db)
pr = PatternRecognitionEngine(db)
pd_eng = PredictionEngine(db)
today = datetime.date.today()
start = today - datetime.timedelta(days=365 * 2)

# All active stocks
stocks = db.query(Stock).filter(Stock.is_active == True).order_by(Stock.symbol).all()
total = len(stocks)

# Symbols that already have a prediction dated today -> skip (makes script resumable)
done_ids = {
    r[0] for r in db.query(Prediction.stock_id)
    .filter(Prediction.timestamp == today)
    .distinct()
    .all()
}

print(f"Total active stocks: {total}. Already done today: {len(done_ids)}. To process: {total - len(done_ids)}", flush=True)

ok = 0
skipped = 0
failed = 0
for i, st in enumerate(stocks, 1):
    sym = st.symbol
    if st.id in done_ids:
        skipped += 1
        continue
    try:
        n = dc.download_historical_ohlcv(sym, start, today + datetime.timedelta(days=1))
        if n > 0:
            ta.calculate_and_save_indicators(sym)
            pr.run_detection_for_stock(sym)
            pd_eng.predict_next_day(sym)
            ok += 1
            status = f"{n} candles OK"
        else:
            failed += 1
            status = "no data"
        if i % 25 == 0 or n > 0:
            print(f"[{i}/{total}] {sym}: {status} | ok={ok} fail={failed} skip={skipped}", flush=True)
    except Exception as e:
        db.rollback()
        failed += 1
        print(f"[{i}/{total}] {sym}: ERROR {str(e)[:120]}", flush=True)
    # gentle pacing to reduce yfinance rate-limiting
    time.sleep(0.4)

print(f"DONE. populated={ok} failed/no-data={failed} skipped={skipped} total={total}", flush=True)
db.close()
