from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.fii_dii import FIIDIIAnalyticsService

router = APIRouter(prefix="/fii-dii", tags=["FII/DII Analytics"])

@router.get("/activity")
def get_fii_dii_activity(limit: int = Query(30, ge=5, le=100), db: Session = Depends(get_db)):
    service = FIIDIIAnalyticsService(db)
    return service.get_historical_activity(limit=limit)

@router.post("/sync")
def trigger_sync(db: Session = Depends(get_db)):
    service = FIIDIIAnalyticsService(db)
    synced_records = service.sync_fii_dii_data()
    return {"status": "success", "synced_records": synced_records}
