from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select, and_
from app.models.models import PriceDaily, PriceIntraday
from typing import List, Optional
import datetime

class PriceRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_daily_prices(self, stock_id: int, start_date: datetime.date, end_date: datetime.date) -> List[PriceDaily]:
        return (
            self.db.query(PriceDaily)
            .filter(
                PriceDaily.stock_id == stock_id,
                PriceDaily.timestamp >= start_date,
                PriceDaily.timestamp <= end_date
            )
            .order_by(PriceDaily.timestamp.asc())
            .all()
        )

    def get_latest_daily_price(self, stock_id: int) -> Optional[PriceDaily]:
        return (
            self.db.query(PriceDaily)
            .filter(PriceDaily.stock_id == stock_id)
            .order_by(PriceDaily.timestamp.desc())
            .first()
        )

    def bulk_upsert_daily(self, prices_data: List[dict]):
        if not prices_data:
            return
        
        # We use PostgreSQL specific upsert
        stmt = insert(PriceDaily)
        upsert_stmt = stmt.on_conflict_do_update(
            constraint="prices_daily_pkey", # Composite primary key constraint
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "adj_close": stmt.excluded.adj_close,
            }
        )
        self.db.execute(upsert_stmt, prices_data)
        self.db.commit()

    def get_intraday_prices(self, stock_id: int, start_time: datetime.datetime, end_time: datetime.datetime, interval: str) -> List[PriceIntraday]:
        return (
            self.db.query(PriceIntraday)
            .filter(
                PriceIntraday.stock_id == stock_id,
                PriceIntraday.interval == interval,
                PriceIntraday.timestamp >= start_time,
                PriceIntraday.timestamp <= end_time
            )
            .order_by(PriceIntraday.timestamp.asc())
            .all()
        )

    def bulk_upsert_intraday(self, prices_data: List[dict]):
        if not prices_data:
            return
            
        stmt = insert(PriceIntraday)
        upsert_stmt = stmt.on_conflict_do_update(
            constraint="prices_intraday_pkey",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            }
        )
        self.db.execute(upsert_stmt, prices_data)
        self.db.commit()
