from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.market_structure import MarketStructureService

router = APIRouter(prefix="/market", tags=["Market Structure"])


@router.get("/sectors")
def get_sector_performance(db: Session = Depends(get_db)):
    """Sector rotation: industry-level average returns over 5/20/60 sessions."""
    return MarketStructureService(db).sector_performance()


@router.get("/breadth")
def get_market_breadth(db: Session = Depends(get_db)):
    """Market breadth: advance/decline, % above key EMAs, new highs/lows, avg RSI."""
    return MarketStructureService(db).market_breadth()
