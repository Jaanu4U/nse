from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.screener import StockScreenerEngine
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/screener", tags=["Stock Screener"])

class ScreenerFilterRequest(BaseModel):
    rsi_min: Optional[float] = None
    rsi_max: Optional[float] = None
    price_above_ema20: Optional[bool] = None
    price_above_ema50: Optional[bool] = None
    price_above_ema200: Optional[bool] = None
    macd_cross: Optional[str] = None # 'BULLISH' or 'BEARISH'
    volume_breakout: Optional[bool] = None
    pe_min: Optional[float] = None
    pe_max: Optional[float] = None
    pb_min: Optional[float] = None
    pb_max: Optional[float] = None
    roe_min: Optional[float] = None
    roce_min: Optional[float] = None
    industry: Optional[str] = None

@router.post("/")
def run_screener(filters: ScreenerFilterRequest, db: Session = Depends(get_db)):
    engine = StockScreenerEngine(db)
    # Convert request model to dictionary, filtering out None values
    filter_dict = {k: v for k, v in filters.model_dump().items() if v is not None}
    return engine.screen_stocks(filter_dict)
