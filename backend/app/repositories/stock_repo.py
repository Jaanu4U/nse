from sqlalchemy.orm import Session
from app.models.models import Stock
from typing import List, Optional

class StockRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_symbol(self, symbol: str) -> Optional[Stock]:
        return self.db.query(Stock).filter(Stock.symbol == symbol.upper()).first()

    def get_by_id(self, stock_id: int) -> Optional[Stock]:
        return self.db.query(Stock).filter(Stock.id == stock_id).first()

    def get_active_stocks(self) -> List[Stock]:
        return self.db.query(Stock).filter(Stock.is_active == True).all()

    def create(self, symbol: str, company_name: str, series: str = "EQ", isin: Optional[str] = None, industry: Optional[str] = None) -> Stock:
        db_stock = Stock(
            symbol=symbol.upper(),
            company_name=company_name,
            series=series,
            isin=isin,
            industry=industry
        )
        self.db.add(db_stock)
        self.db.commit()
        self.db.refresh(db_stock)
        return db_stock

    def bulk_create_or_update(self, stocks_data: List[dict]) -> List[Stock]:
        # Perform upserts
        updated_stocks = []
        for s in stocks_data:
            existing = self.get_by_symbol(s['symbol'])
            if existing:
                existing.company_name = s.get('company_name', existing.company_name)
                existing.isin = s.get('isin', existing.isin)
                existing.industry = s.get('industry', existing.industry)
                existing.is_active = s.get('is_active', existing.is_active)
                updated_stocks.append(existing)
            else:
                new_stock = Stock(
                    symbol=s['symbol'].upper(),
                    company_name=s['company_name'],
                    series=s.get('series', 'EQ'),
                    isin=s.get('isin'),
                    industry=s.get('industry')
                )
                self.db.add(new_stock)
                updated_stocks.append(new_stock)
        self.db.commit()
        return updated_stocks
