import datetime, logging, time
logging.basicConfig(level=logging.ERROR)
from sqlalchemy import func
from app.database import SessionLocal
from app.models.models import Stock, PriceDaily
from app.services.data_collection import DataCollectionEngine
from app.services.technical_analysis import TechnicalAnalysisEngine
from app.services.pattern_recognition import PatternRecognitionEngine
from app.services.prediction import PredictionEngine
from app.utils.job_progress import start_job, set_progress, finish_job, fail_job

db = SessionLocal()
dc = DataCollectionEngine(db)
ta = TechnicalAnalysisEngine(db)
pr = PatternRecognitionEngine(db)
pd_eng = PredictionEngine(db)

today = datetime.date.today()
start = today - datetime.timedelta(days=365 * 2)
recent_cutoff = today - datetime.timedelta(days=7)

stocks = db.query(Stock).filter(Stock.is_active == True).order_by(Stock.symbol).all()
total = len(stocks)
start_job("full_regen", total=total, message="Starting full data regeneration")

# Existing price coverage: stock_id -> (latest_date, candle_count)
price_info = {
    sid: (mx, cnt)
    for sid, mx, cnt in db.query(
        PriceDaily.stock_id, func.max(PriceDaily.timestamp), func.count()
    ).group_by(PriceDaily.stock_id).all()
}

print(f"Total active stocks: {total}. Already have price data: {len(price_info)}", flush=True)

# Ensure the NIFTY benchmark is available for relative-strength / beta features.
try:
    idx_n = dc.ensure_index()
    print(f"NIFTY benchmark index ready: {idx_n} candles", flush=True)
except Exception as e:
    print(f"WARN: could not seed NIFTY index: {str(e)[:120]}", flush=True)

ok = 0; regen = 0; dl = 0; fail = 0
for i, st in enumerate(stocks, 1):
    sym = st.symbol
    info = price_info.get(st.id)
    has_recent = bool(info and info[1] >= 100 and info[0] >= recent_cutoff)
    try:
        if has_recent:
            n = info[1]
        else:
            n = dc.download_historical_ohlcv(sym, start, today + datetime.timedelta(days=1))
            dl += 1
        if n and n >= 100:
            ta.calculate_and_save_indicators(sym)
            pr.run_detection_for_stock(sym)
            pd_eng.predict_next_day(sym)
            ok += 1
            if has_recent:
                regen += 1
        else:
            fail += 1
        if i % 25 == 0 or not has_recent:
            print(f"[{i}/{total}] {sym}: {'regen' if has_recent else 'download'} | ok={ok} regen={regen} dl={dl} fail={fail}", flush=True)
    except Exception as e:
        db.rollback(); fail += 1
        print(f"[{i}/{total}] {sym}: ERROR {str(e)[:100]}", flush=True)
    if i % 10 == 0 or i == total:
        set_progress(current=i, message=f"Updating {sym} ({i}/{total})")
    if not has_recent:
        time.sleep(0.4)

print(f"DONE ok={ok} regen={regen} downloaded={dl} fail={fail} total={total}", flush=True)
finish_job(message=f"Update complete · {ok} stocks refreshed ({fail} skipped)")
db.close()
