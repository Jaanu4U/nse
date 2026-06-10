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


@router.get("/top-picks")
def get_top_picks(limit: int = 25, db: Session = Depends(get_db)):
    """Return the top high-probability stocks ranked by composite ML score."""
    engine = StockScreenerEngine(db)
    return engine.get_top_picks(limit=limit)


@router.get("/breakout-ready")
def get_breakout_ready(limit: int = 30, min_readiness: int = 55, db: Session = Depends(get_db)):
    """Scan the universe for stocks coiled and ready to break out, ranked by readiness."""
    from app.services.breakout_detection import BreakoutDetectionEngine
    engine = BreakoutDetectionEngine(db)
    return engine.scan_breakout_ready(limit=limit, min_readiness=min_readiness)


@router.get("/breakout-catalog")
def get_breakout_catalog():
    """Reference catalog of breakout patterns with advantages and disadvantages."""
    from app.services.breakout_detection import BREAKOUT_CATALOG
    return [
        {"pattern": key, **meta}
        for key, meta in BREAKOUT_CATALOG.items()
    ]


@router.post("/picks/snapshot")
def snapshot_daily_picks(limit: int = 25, force: bool = False, db: Session = Depends(get_db)):
    """Archive today's Top-N high-probability picks for later scoring."""
    from app.services.daily_picks import DailyPicksService
    service = DailyPicksService(db)
    return service.snapshot_today(limit=limit, force=force)


@router.post("/picks/evaluate")
def evaluate_daily_picks(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Fetch realised OHLC and flag WIN/LOSS for an archived picks day (default today)."""
    import datetime
    from app.services.daily_picks import DailyPicksService
    service = DailyPicksService(db)
    pick_date = datetime.datetime.strptime(date, "%Y-%m-%d").date() if date else None
    return service.evaluate_picks(pick_date=pick_date)


@router.get("/picks/dates")
def list_picks_dates(limit: int = 30, db: Session = Depends(get_db)):
    """List the archived pick dates (most recent first)."""
    from app.services.daily_picks import DailyPicksService
    return DailyPicksService(db).list_dates(limit=limit)


@router.get("/picks/report")
def get_picks_report(date: Optional[str] = None, refresh: bool = False, db: Session = Depends(get_db)):
    """
    Scorecard for an archived picks day: the 25 stocks with their entry price plus
    today's live/closing open, high, low and current price, and a win/loss summary.
    Defaults to the most recent archived day. Pass refresh=true to pull live quotes.
    """
    import datetime
    from app.services.daily_picks import DailyPicksService
    service = DailyPicksService(db)
    pick_date = datetime.datetime.strptime(date, "%Y-%m-%d").date() if date else None
    return service.get_report(pick_date=pick_date, refresh=refresh)
