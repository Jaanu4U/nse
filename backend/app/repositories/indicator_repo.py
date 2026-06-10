from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app.models.models import TechnicalIndicator
from typing import List, Optional
import datetime

class IndicatorRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_indicators(self, stock_id: int, start_date: datetime.date, end_date: datetime.date) -> List[TechnicalIndicator]:
        return (
            self.db.query(TechnicalIndicator)
            .filter(
                TechnicalIndicator.stock_id == stock_id,
                TechnicalIndicator.timestamp >= start_date,
                TechnicalIndicator.timestamp <= end_date
            )
            .order_by(TechnicalIndicator.timestamp.asc())
            .all()
        )

    def bulk_upsert(self, indicator_data: List[dict]):
        if not indicator_data:
            return
            
        stmt = insert(TechnicalIndicator)
        upsert_stmt = stmt.on_conflict_do_update(
            constraint="technical_indicators_pkey",
            set_={
                "rsi": stmt.excluded.rsi,
                "macd": stmt.excluded.macd,
                "macd_signal": stmt.excluded.macd_signal,
                "macd_hist": stmt.excluded.macd_hist,
                "ema_20": stmt.excluded.ema_20,
                "ema_50": stmt.excluded.ema_50,
                "ema_200": stmt.excluded.ema_200,
                "sma_20": stmt.excluded.sma_20,
                "bb_upper": stmt.excluded.bb_upper,
                "bb_middle": stmt.excluded.bb_middle,
                "bb_lower": stmt.excluded.bb_lower,
                "vwap": stmt.excluded.vwap,
                "atr": stmt.excluded.atr,
                "adx": stmt.excluded.adx,
                "ichimoku_tenkan": stmt.excluded.ichimoku_tenkan,
                "ichimoku_kijun": stmt.excluded.ichimoku_kijun,
                "ichimoku_senkou_a": stmt.excluded.ichimoku_senkou_a,
                "ichimoku_senkou_b": stmt.excluded.ichimoku_senkou_b,
                "stoch_k": stmt.excluded.stoch_k,
                "stoch_d": stmt.excluded.stoch_d,
                "mfi": stmt.excluded.mfi,
                "cci": stmt.excluded.cci,
                "williams_r": stmt.excluded.williams_r,
                "obv": stmt.excluded.obv,
                "supertrend": stmt.excluded.supertrend,
                "supertrend_dir": stmt.excluded.supertrend_dir,
            }
        )
        self.db.execute(upsert_stmt, indicator_data)
        self.db.commit()
