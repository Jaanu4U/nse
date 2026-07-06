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
import os

logger = logging.getLogger(__name__)

def run_daily_sync():
    """
    Complete end-to-end stock sync, analysis, pattern recognition, and predictions.
    """
    logger.info("Starting scheduled daily data sync and analysis job...")
    start_job("daily_sync", total=10, message="Starting daily market update")
    db = SessionLocal()
    try:
        # 1. Sync Stock Master
        set_progress(current=1, message="Syncing stock master list")
        collector = DataCollectionEngine(db)
        collector.sync_stock_master()
        
        # 2. Ingest daily prices (incremental update)
        set_progress(current=2, message="Downloading latest prices")
        collector.incremental_update()

        # 2b. Prune the tradable universe to the liquid tier so all downstream
        # work (indicators, predictions, picks) runs only on liquid names.
        collector.prune_to_liquid_universe()
        
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

        # 10. Grade the prior session's 3:20 PM intraday-strategy picks now that
        # today's OHLC is ingested, so the scorecard shows the just-completed day
        # immediately instead of waiting for the 01:00 evaluation job.
        set_progress(current=10, message="Grading 3:20 PM strategy picks")
        try:
            from app.services.intraday_strategy import IntradayStrategyService
            IntradayStrategyService(db).evaluate()
        except Exception as ie:
            logger.error(f"Intraday strategy evaluation in daily sync failed: {ie}")

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

    # "Strategy 3% · 3:20 PM": ten minutes before the NSE close, treat the live price as
    # today's close, re-run the prediction pipeline on the liquid universe and snapshot
    # the Top-5 by P(+3%) so they are actionable before the 15:30 close.
    def intraday_strategy_job():
        logger.info("Running 3:20 PM intraday strategy (Top-5 by P(+3%))...")
        db = SessionLocal()
        try:
            from app.services.intraday_strategy import IntradayStrategyService
            IntradayStrategyService(db).run_strategy(top_n=5, force=True)
        except Exception as e:
            logger.error(f"Error running intraday strategy: {e}")
        finally:
            db.close()

    scheduler.add_job(
        intraday_strategy_job,
        'cron',
        day_of_week='mon-fri',
        hour=15,
        minute=20,
        id='intraday_strategy_job'
    )

    # 18:15 IST Mon-Fri — update regime context (VIX, expiry, gap day, Nifty trend)
    def update_regime_job():
        logger.info("[REGIME] Updating daily regime context...")
        try:
            from app.services.regime_service import update_regime_context
            result = update_regime_context()
            logger.info("[REGIME] Done: %s", result.get("regime_note", ""))
        except Exception as e:
            logger.error(f"[REGIME] Regime update failed: {e}")

    scheduler.add_job(
        update_regime_job,
        'cron',
        day_of_week='mon-fri',
        hour=18,
        minute=15,
        id='update_regime_job'
    )

    # Every Sunday 02:00 IST — retrain Level 2 (historical profiles) and Level 3
    # (XGBoost ML model) using the last 60 days of accumulated delta minute candles.
    # Keeps impact_coeff, up_prob, and ml_up_prob current as new data builds up.
    def retrain_delta_models_job():
        logger.info("[DELTA-RETRAIN] Starting weekly Level 2 + Level 3 retraining...")
        import subprocess, sys
        try:
            result = subprocess.run(
                [sys.executable, "/workspace/train_delta_models.py", "--level", "all", "--days", "60"],
                capture_output=True, text=True, timeout=1800  # 30 min max
            )
            if result.returncode == 0:
                logger.info("[DELTA-RETRAIN] Level 2 + Level 3 retraining completed successfully.")
            else:
                logger.error(f"[DELTA-RETRAIN] Retraining failed:\n{result.stderr[-2000:]}")
        except subprocess.TimeoutExpired:
            logger.error("[DELTA-RETRAIN] Retraining timed out after 30 minutes.")
        except Exception as e:
            logger.error(f"[DELTA-RETRAIN] Retraining error: {e}")

    scheduler.add_job(
        retrain_delta_models_job,
        'cron',
        day_of_week='sun',
        hour=2,
        minute=0,
        id='retrain_delta_models_job'
    )

    # C6 FIX: Re-score all symbols with the current ML model every trading morning at
    # 09:00 IST using yesterday's complete candle data — predictions are no longer stale
    # from the prior session.
    def rescore_ml_predictions_job():
        logger.info("[ML-RESCORE] Regenerating daily ML predictions at 09:00...")
        import subprocess, sys
        try:
            result = subprocess.run(
                [sys.executable, "/workspace/train_delta_models.py", "--score-only"],
                capture_output=True, text=True, timeout=300  # 5 min max for scoring-only pass
            )
            if result.returncode == 0:
                logger.info("[ML-RESCORE] ML predictions refreshed successfully.")
            else:
                logger.error(f"[ML-RESCORE] Rescore failed:\n{result.stderr[-1000:]}")
        except subprocess.TimeoutExpired:
            logger.error("[ML-RESCORE] Rescore timed out after 5 minutes.")
        except Exception as e:
            logger.error(f"[ML-RESCORE] Rescore error: {e}")

    scheduler.add_job(
        rescore_ml_predictions_job,
        'cron',
        day_of_week='mon-fri',
        hour=9,
        minute=0,
        id='rescore_ml_predictions_job'
    )

    # EOD accuracy tracking job — 16:00 IST Mon-Fri.
    # Computes directional hit-rate, profit factor and calibration for delta_prediction_history.
    # Closes the feedback loop from the audit roadmap.
    def eod_accuracy_job():
        logger.info("[ACCURACY] Computing EOD prediction accuracy metrics...")
        import psycopg2
        import os
        try:
            conn = psycopg2.connect(
                host=os.getenv("DB_HOST", "db"),
                port=int(os.getenv("DB_PORT", 5432)),
                dbname=os.getenv("DB_NAME", "nse_stock_db"),
                user=os.getenv("DB_USER", "postgres"),
                password=os.getenv("DB_PASS", "postgrespassword"),
            )
            today = datetime.date.today()
            with conn.cursor() as cur:
                # Resolve today's delta_prediction_history rows against realised close prices.
                # A prediction is a "HIT" when:
                #   BULLISH signal → next candle close > entry price (positive move)
                #   BEARISH signal → next candle close < entry price (negative move)
                cur.execute("""
                    INSERT INTO delta_prediction_accuracy
                      (trade_date, total_predictions, hits, hit_rate,
                       avg_predicted_score, avg_realised_move,
                       profit_factor, computed_at)
                    WITH resolved AS (
                        SELECT
                            ph.symbol,
                            ph.signal,
                            ph.prediction_score,
                            ph.created_at,
                            -- 15-min forward return
                            CASE WHEN mc_now.close_price > 0
                                 THEN (mc_fwd.close_price - mc_now.close_price) / mc_now.close_price * 100
                                 ELSE NULL END AS realised_move
                        FROM delta_prediction_history ph
                        JOIN delta_minute_candle mc_now
                          ON mc_now.symbol = ph.symbol
                         AND mc_now.minute_ts = date_trunc('minute', ph.created_at)
                        JOIN delta_minute_candle mc_fwd
                          ON mc_fwd.symbol = ph.symbol
                         AND mc_fwd.minute_ts = date_trunc('minute', ph.created_at) + INTERVAL '15 minutes'
                        WHERE ph.created_at::date = %s
                          AND ph.signal IN ('BULLISH', 'BEARISH')
                    ),
                    scored AS (
                        SELECT *,
                            CASE WHEN signal = 'BULLISH' AND realised_move > 0 THEN 1
                                 WHEN signal = 'BEARISH' AND realised_move < 0 THEN 1
                                 ELSE 0 END AS is_hit
                        FROM resolved WHERE realised_move IS NOT NULL
                    )
                    SELECT
                        %s,
                        COUNT(*),
                        SUM(is_hit),
                        ROUND(AVG(is_hit) * 100, 2),
                        ROUND(AVG(prediction_score), 2),
                        ROUND(AVG(realised_move), 4),
                        ROUND(
                            SUM(CASE WHEN is_hit = 1 THEN ABS(realised_move) ELSE 0 END) /
                            NULLIF(SUM(CASE WHEN is_hit = 0 THEN ABS(realised_move) ELSE 0 END), 0)
                        , 3),
                        NOW()
                    FROM scored
                    ON CONFLICT (trade_date) DO UPDATE SET
                        total_predictions = EXCLUDED.total_predictions,
                        hits              = EXCLUDED.hits,
                        hit_rate          = EXCLUDED.hit_rate,
                        avg_predicted_score = EXCLUDED.avg_predicted_score,
                        avg_realised_move   = EXCLUDED.avg_realised_move,
                        profit_factor     = EXCLUDED.profit_factor,
                        computed_at       = EXCLUDED.computed_at
                """, (today, today))
                conn.commit()
                # Log summary
                cur.execute("SELECT total_predictions, hits, hit_rate, profit_factor FROM delta_prediction_accuracy WHERE trade_date = %s", (today,))
                row = cur.fetchone()
                if row:
                    logger.info("[ACCURACY] %s — %d predictions, %d hits (%.1f%%), profit factor=%.2f",
                                today, row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0)
        except Exception as e:
            logger.error(f"[ACCURACY] EOD accuracy job failed: {e}")
        finally:
            try: conn.close()
            except: pass

    scheduler.add_job(
        eod_accuracy_job,
        'cron',
        day_of_week='mon-fri',
        hour=16,
        minute=0,
        id='eod_accuracy_job'
    )

    scheduler.start()
    logger.info("APScheduler initialized and started successfully.")
    return scheduler
