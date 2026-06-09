from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.insider_trading import InsiderTradingService

router = APIRouter(prefix="/stocks", tags=["Insider Trading Tracker"])

@router.get("/{symbol}/insider-trades")
def get_insider_trades(symbol: str, limit: int = Query(20, ge=5, le=100), db: Session = Depends(get_db)):
    service = InsiderTradingService(db)
    return service.get_stock_insider_trades(symbol, limit=limit)

@router.post("/{symbol}/insider-trades/sync")
def trigger_insider_sync(symbol: str, db: Session = Depends(get_db)):
    service = InsiderTradingService(db)
    synced = service.sync_insider_trades(symbol)
    return {"status": "success", "synced_records": synced}
