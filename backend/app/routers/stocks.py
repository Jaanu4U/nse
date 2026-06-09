from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.repositories.stock_repo import StockRepository
from app.repositories.price_repo import PriceRepository
from app.repositories.indicator_repo import IndicatorRepository
from app.services.supply_demand import SupplyDemandEngine
from app.services.pattern_recognition import PatternRecognitionEngine
from app.services.prediction import PredictionEngine
from app.services.similarity import SimilarityEngine
from app.services.sentiment_analysis import SentimentAnalysisEngine
from app.services.ai_analyst import AIAnalystService
from app.models.models import News
import datetime
from typing import List, Dict, Any, Optional

router = APIRouter(prefix="/stocks", tags=["Stocks & Analysis"])

@router.get("/")
def get_stocks(db: Session = Depends(get_db)):
    repo = StockRepository(db)
    return repo.get_active_stocks()

@router.get("/{symbol}")
def get_stock_by_symbol(symbol: str, db: Session = Depends(get_db)):
    repo = StockRepository(db)
    stock = repo.get_by_symbol(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")
    return stock

@router.get("/{symbol}/chart/daily")
def get_daily_chart(symbol: str, start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db)):
    stock_repo = StockRepository(db)
    price_repo = PriceRepository(db)
    stock = stock_repo.get_by_symbol(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")

    start_date = datetime.datetime.strptime(start, "%Y-%m-%d").date() if start else (datetime.date.today() - datetime.timedelta(days=365))
    end_date = datetime.datetime.strptime(end, "%Y-%m-%d").date() if end else datetime.date.today()

    prices = price_repo.get_daily_prices(stock.id, start_date, end_date)
    if not prices:
        # Lazy-load daily/intraday prices and technical analysis for the stock
        try:
            from app.services.data_collection import DataCollectionEngine
            from app.services.technical_analysis import TechnicalAnalysisEngine
            from app.services.pattern_recognition import PatternRecognitionEngine
            from app.services.prediction import PredictionEngine
            
            collector = DataCollectionEngine(db)
            # Load 3 years of data
            collector.download_historical_ohlcv(stock.symbol, datetime.date.today() - datetime.timedelta(days=365 * 3), datetime.date.today() + datetime.timedelta(days=1))
            collector.download_intraday_ohlcv(stock.symbol, interval="15m", period="5d")
            
            ta = TechnicalAnalysisEngine(db)
            ta.calculate_and_save_indicators(stock.symbol)
            
            patterns = PatternRecognitionEngine(db)
            patterns.run_detection_for_stock(stock.symbol)
            
            pred = PredictionEngine(db)
            pred.predict_next_day(stock.symbol)
            
            prices = price_repo.get_daily_prices(stock.id, start_date, end_date)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to auto-populate daily data for {symbol}: {e}")

    # Format for TradingView Lightweight charts: { time: 'YYYY-MM-DD', open, high, low, close, volume }
    return [{
        "time": p.timestamp.strftime("%Y-%m-%d"),
        "open": float(p.open),
        "high": float(p.high),
        "low": float(p.low),
        "close": float(p.close),
        "volume": int(p.volume)
    } for p in prices]

@router.get("/{symbol}/chart/intraday")
def get_intraday_chart(symbol: str, interval: str = "15m", db: Session = Depends(get_db)):
    stock_repo = StockRepository(db)
    price_repo = PriceRepository(db)
    stock = stock_repo.get_by_symbol(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")

    start_time = datetime.datetime.now() - datetime.timedelta(days=7)
    prices = price_repo.get_intraday_prices(stock.id, start_time, datetime.datetime.now(), interval)
    if not prices:
        try:
            from app.services.data_collection import DataCollectionEngine
            collector = DataCollectionEngine(db)
            collector.download_intraday_ohlcv(stock.symbol, interval=interval, period="7d")
            prices = price_repo.get_intraday_prices(stock.id, start_time, datetime.datetime.now(), interval)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to auto-populate intraday data for {symbol}: {e}")
            
    return [{
        "time": int(p.timestamp.timestamp()),  # Unix timestamp
        "open": float(p.open),
        "high": float(p.high),
        "low": float(p.low),
        "close": float(p.close),
        "volume": int(p.volume)
    } for p in prices]

@router.get("/{symbol}/analysis")
def get_technical_analysis(symbol: str, db: Session = Depends(get_db)):
    """
    Get aggregated support/resistance, breakouts, detected patterns, and predictions.
    """
    sd_engine = SupplyDemandEngine(db)
    pred_engine = PredictionEngine(db)
    
    zones = sd_engine.detect_support_resistance(symbol)
    breakouts = sd_engine.detect_breakouts(symbol)
    preds = pred_engine.predict_next_day(symbol)
    
    # Get patterns from db
    stock_repo = StockRepository(db)
    stock = stock_repo.get_by_symbol(symbol)
    patterns = []
    if stock:
        # Load from last 10 days
        from app.models.models import DetectedPattern
        patterns = db.query(DetectedPattern).filter(
            DetectedPattern.stock_id == stock.id,
            DetectedPattern.timestamp >= (datetime.date.today() - datetime.timedelta(days=10))
        ).order_by(DetectedPattern.timestamp.desc()).all()

    return {
        "supports": zones["supports"],
        "resistances": zones["resistances"],
        "breakout": breakouts,
        "predictions": preds,
        "patterns": [{
            "pattern_name": p.pattern_name,
            "signal_type": p.signal_type,
            "confidence": float(p.confidence),
            "date": p.timestamp.strftime("%Y-%m-%d")
        } for p in patterns]
    }

@router.get("/{symbol}/similarity")
def get_similar_patterns(symbol: str, k: int = 5, db: Session = Depends(get_db)):
    engine = SimilarityEngine(db)
    return engine.find_similar_patterns(symbol, k=k)

@router.get("/{symbol}/news")
def get_stock_news(symbol: str, db: Session = Depends(get_db)):
    stock_repo = StockRepository(db)
    stock = stock_repo.get_by_symbol(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")
        
    # Trigger refresh
    sentiment_engine = SentimentAnalysisEngine(db)
    sentiment_engine.fetch_and_analyze_news(symbol)

    news_items = db.query(News).filter(News.stock_id == stock.id).order_by(News.published_at.desc()).limit(10).all()
    return [{
        "title": n.title,
        "url": n.url,
        "source": n.source,
        "published_at": n.published_at.isoformat(),
        "sentiment_score": float(n.sentiment_score) if n.sentiment_score is not None else 0.0,
        "sentiment_class": n.sentiment_class
    } for n in news_items]

@router.get("/{symbol}/ai-report")
def get_ai_report(symbol: str, db: Session = Depends(get_db)):
    analyst = AIAnalystService(db)
    report_md = analyst.generate_stock_report(symbol)
    return {"report": report_md}
