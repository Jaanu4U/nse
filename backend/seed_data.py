import datetime, logging, sys
logging.basicConfig(level=logging.WARNING)
from app.database import SessionLocal
from app.services.data_collection import DataCollectionEngine
from app.services.technical_analysis import TechnicalAnalysisEngine
from app.services.pattern_recognition import PatternRecognitionEngine
from app.services.prediction import PredictionEngine

NIFTY50 = [
 "RELIANCE","TCS","HDFCBANK","ICICIBANK","INFY","HINDUNILVR","ITC","SBIN","BHARTIARTL","LT",
 "KOTAKBANK","AXISBANK","BAJFINANCE","ASIANPAINT","MARUTI","HCLTECH","SUNPHARMA","TITAN","ULTRACEMCO","WIPRO",
 "ONGC","NTPC","POWERGRID","NESTLEIND","TATAMOTORS","TATASTEEL","JSWSTEEL","ADANIENT","ADANIPORTS","COALINDIA",
 "BAJAJFINSV","GRASIM","HINDALCO","DRREDDY","CIPLA","BRITANNIA","EICHERMOT","HEROMOTOCO","BAJAJ-AUTO","DIVISLAB",
 "TECHM","INDUSINDBK","APOLLOHOSP","TATACONSUM","BPCL","SBILIFE","HDFCLIFE","M&M","SHRIRAMFIN","LTIM",
]

db = SessionLocal()
dc = DataCollectionEngine(db)
ta = TechnicalAnalysisEngine(db)
pr = PatternRecognitionEngine(db)
pd_eng = PredictionEngine(db)
today = datetime.date.today()
start = today - datetime.timedelta(days=365*2)

ok=0
for i,sym in enumerate(NIFTY50,1):
    try:
        n = dc.download_historical_ohlcv(sym, start, today + datetime.timedelta(days=1))
        if n>0:
            ta.calculate_and_save_indicators(sym)
            pr.run_detection_for_stock(sym)
            pd_eng.predict_next_day(sym)
            ok+=1
        print(f"[{i}/{len(NIFTY50)}] {sym}: {n} candles", flush=True)
    except Exception as e:
        print(f"[{i}/{len(NIFTY50)}] {sym}: ERROR {e}", flush=True)
print(f"DONE. {ok}/{len(NIFTY50)} stocks populated.", flush=True)
db.close()
