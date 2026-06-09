from apscheduler.schedulers.background import BackgroundScheduler
from app.database import SessionLocal
from app.services.data_collection import DataCollectionEngine
from app.services.technical_analysis import TechnicalAnalysisEngine
from app.services.pattern_recognition import PatternRecognitionEngine
from app.services.prediction import PredictionEngine
from app.services.sentiment_analysis import SentimentAnalysisEngine
from app.services.corporate_actions import CorporateActionsService
import logging
import datetime

logger = logging.getLogger(__name__)

def run_daily_sync():
    """
    Complete end-to-end stock sync, analysis, pattern recognition, and predictions.
    """
    logger.info("Starting scheduled daily data sync and analysis job...")
    db = SessionLocal()
    try:
        # 1. Sync Stock Master
        collector = DataCollectionEngine(db)
        collector.sync_stock_master()
        
        # 2. Ingest daily prices (incremental update)
        collector.incremental_update()
        
        # 3. Apply corporate actions
        actions = CorporateActionsService(db)
        actions.apply_pending_actions()
        
        # 4. Calculate indicators
        ta = TechnicalAnalysisEngine(db)
        ta.calculate_all_active_stocks()
        
        # 5. Detect patterns
        patterns = PatternRecognitionEngine(db)
        patterns.run_all_stocks()
        
        # 6. Predict moves
        pred = PredictionEngine(db)
        pred.predict_all_stocks()
        
        # 7. Collect news sentiment
        sentiment = SentimentAnalysisEngine(db)
        sentiment.analyze_all_stocks()

        # 8. Sync FII/DII activities
        from app.services.fii_dii import FIIDIIAnalyticsService
        fii_dii_service = FIIDIIAnalyticsService(db)
        fii_dii_service.sync_fii_dii_data()

        # 9. Sync Insider Trading activities for active stocks
        from app.services.insider_trading import InsiderTradingService
        from app.repositories.stock_repo import StockRepository
        insider_service = InsiderTradingService(db)
        stock_repo = StockRepository(db)
        active_stocks = stock_repo.get_active_stocks()
        for stock in active_stocks:
            insider_service.sync_insider_trades(stock.symbol)
        
        logger.info("Daily sync and analysis job completed successfully.")
        
    except Exception as e:
        logger.error(f"Error executing daily sync job: {e}")
    finally:
        db.close()

def start_scheduler():
    scheduler = BackgroundScheduler()
    
    # Run daily updates at 17:00 (5:00 PM) after market close (Mon-Fri)
    scheduler.add_job(
        run_daily_sync,
        'cron',
        day_of_week='mon-fri',
        hour=17,
        minute=0,
        id='daily_sync_job'
    )
    
    # Run news sentiment analysis hourly
    def hourly_news_job():
        logger.info("Starting hourly news sentiment sync...")
        db = SessionLocal()
        try:
            sentiment = SentimentAnalysisEngine(db)
            sentiment.analyze_all_stocks()
        except Exception as e:
            logger.error(f"Error in hourly news sync: {e}")
        finally:
            db.close()
            
    scheduler.add_job(
        hourly_news_job,
        'interval',
        hours=1,
        id='hourly_news_job'
    )
    
    scheduler.start()
    logger.info("APScheduler initialized and started successfully.")
    return scheduler
