from apscheduler.schedulers.background import BackgroundScheduler
from app.database import SessionLocal
from app.services.data_collection import DataCollectionEngine
from app.services.technical_analysis import TechnicalAnalysisEngine
from app.services.pattern_recognition import PatternRecognitionEngine
from app.services.prediction import PredictionEngine
from app.services.sentiment_analysis import SentimentAnalysisEngine
from app.services.corporate_actions import CorporateActionsService
from app.utils.job_progress import start_job, set_progress, finish_job, fail_job
import logging
import datetime

logger = logging.getLogger(__name__)

def run_daily_sync():
    """
    Complete end-to-end stock sync, analysis, pattern recognition, and predictions.
    """
    logger.info("Starting scheduled daily data sync and analysis job...")
    start_job("daily_sync", total=9, message="Starting daily market update")
    db = SessionLocal()
    try:
        # 1. Sync Stock Master
        set_progress(current=1, message="Syncing stock master list")
        collector = DataCollectionEngine(db)
        collector.sync_stock_master()
        
        # 2. Ingest daily prices (incremental update)
        set_progress(current=2, message="Downloading latest prices")
        collector.incremental_update()
        
        # 3. Apply corporate actions
        set_progress(current=3, message="Applying corporate actions")
        actions = CorporateActionsService(db)
        actions.apply_pending_actions()
        
        # 4. Calculate indicators
        set_progress(current=4, message="Calculating technical indicators")
        ta = TechnicalAnalysisEngine(db)
        ta.calculate_all_active_stocks()
        
        # 5. Detect patterns
        set_progress(current=5, message="Detecting chart & breakout patterns")
        patterns = PatternRecognitionEngine(db)
        patterns.run_all_stocks()
        
        # 6. Predict moves
        set_progress(current=6, message="Generating ML predictions")
        pred = PredictionEngine(db)
        pred.predict_all_stocks()
        
        # 7. Collect news sentiment
        set_progress(current=7, message="Analyzing news sentiment")
        sentiment = SentimentAnalysisEngine(db)
        sentiment.analyze_all_stocks()

        # 8. Sync FII/DII activities
        set_progress(current=8, message="Syncing FII/DII flows")
        from app.services.fii_dii import FIIDIIAnalyticsService
        fii_dii_service = FIIDIIAnalyticsService(db)
        fii_dii_service.sync_fii_dii_data()

        # 9. Sync Insider Trading activities for active stocks
        set_progress(current=9, message="Syncing insider trades")
        from app.services.insider_trading import InsiderTradingService
        from app.repositories.stock_repo import StockRepository
        insider_service = InsiderTradingService(db)
        stock_repo = StockRepository(db)
        active_stocks = stock_repo.get_active_stocks()
        for stock in active_stocks:
            insider_service.sync_insider_trades(stock.symbol)
        
        logger.info("Daily sync and analysis job completed successfully.")
        finish_job(message="Daily market update complete")
        
    except Exception as e:
        logger.error(f"Error executing daily sync job: {e}")
        fail_job(message=f"Daily update failed: {str(e)[:120]}")
    finally:
        db.close()

def start_scheduler():
    # Run all cron jobs in India Standard Time so schedule times are predictable
    # regardless of the container's system timezone (which is UTC).
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

    # Run daily full sync at 18:00 IST (after NSE close 15:30 + EOD data settle), Mon-Fri
    scheduler.add_job(
        run_daily_sync,
        'cron',
        day_of_week='mon-fri',
        hour=18,
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

    # Snapshot the Top-25 high-probability picks every trading morning (09:10 IST,
    # before market open) so the day's recommendations are archived for scoring.
    def snapshot_picks_job():
        logger.info("Snapshotting daily Top-25 picks...")
        db = SessionLocal()
        try:
            from app.services.daily_picks import DailyPicksService
            DailyPicksService(db).snapshot_today()
        except Exception as e:
            logger.error(f"Error snapshotting daily picks: {e}")
        finally:
            db.close()

    scheduler.add_job(
        snapshot_picks_job,
        'cron',
        day_of_week='mon-fri',
        hour=9,
        minute=10,
        id='snapshot_picks_job'
    )

    # After market close (15:45 IST) score how many of today's picks worked out
    # by fetching the realised intraday OHLC and flagging WIN/LOSS.
    def evaluate_picks_job():
        logger.info("Evaluating daily Top-25 picks after close...")
        db = SessionLocal()
        try:
            from app.services.daily_picks import DailyPicksService
            DailyPicksService(db).evaluate_picks()
        except Exception as e:
            logger.error(f"Error evaluating daily picks: {e}")
        finally:
            db.close()

    scheduler.add_job(
        evaluate_picks_job,
        'cron',
        day_of_week='mon-fri',
        hour=15,
        minute=45,
        id='evaluate_picks_job'
    )

    scheduler.start()
    logger.info("APScheduler initialized and started successfully.")
    return scheduler
