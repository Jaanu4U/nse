from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.models.models import Financial, QuarterlyResult
from typing import List, Optional


class FinancialsRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_latest(self, stock_id: int) -> Optional[Financial]:
        return (
            self.db.query(Financial)
            .filter(Financial.stock_id == stock_id)
            .order_by(desc(Financial.fiscal_year), desc(Financial.created_at))
            .first()
        )

    def upsert_annual(self, stock_id: int, fiscal_year: int, data: dict) -> Financial:
        """
        Insert or update the annual financial snapshot for (stock_id, fiscal_year).
        There is no DB unique constraint, so we look up the row manually.
        """
        existing = (
            self.db.query(Financial)
            .filter(Financial.stock_id == stock_id, Financial.fiscal_year == fiscal_year)
            .first()
        )
        fields = ("revenue", "net_income", "eps", "pe", "pb", "roe", "roce",
                  "debt_to_equity", "free_cash_flow")
        if existing:
            for f in fields:
                if data.get(f) is not None:
                    setattr(existing, f, data[f])
            self.db.commit()
            return existing

        row = Financial(stock_id=stock_id, fiscal_year=fiscal_year,
                        **{f: data.get(f) for f in fields})
        self.db.add(row)
        self.db.commit()
        return row

    def get_quarterly(self, stock_id: int, limit: int = 8) -> List[QuarterlyResult]:
        return (
            self.db.query(QuarterlyResult)
            .filter(QuarterlyResult.stock_id == stock_id)
            .order_by(desc(QuarterlyResult.quarter))
            .limit(limit)
            .all()
        )

    def upsert_quarterly(self, stock_id: int, quarter: str, data: dict) -> QuarterlyResult:
        existing = (
            self.db.query(QuarterlyResult)
            .filter(QuarterlyResult.stock_id == stock_id, QuarterlyResult.quarter == quarter)
            .first()
        )
        fields = ("revenue", "net_income", "eps", "operating_profit_margin")
        if existing:
            for f in fields:
                if data.get(f) is not None:
                    setattr(existing, f, data[f])
            self.db.commit()
            return existing

        row = QuarterlyResult(stock_id=stock_id, quarter=quarter,
                              **{f: data.get(f) for f in fields})
        self.db.add(row)
        self.db.commit()
        return row
