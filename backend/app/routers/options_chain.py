from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.options_chain import OptionsChainService

router = APIRouter(prefix="/stocks", tags=["Options Chain Analyzer"])

@router.get("/{symbol}/options-chain")
def get_options_chain(symbol: str, db: Session = Depends(get_db)):
    service = OptionsChainService(db)
    result = service.get_options_chain(symbol)
    if not result["strikes"]:
        raise HTTPException(status_code=404, detail="Option chain data not available for this ticker.")
    return result
