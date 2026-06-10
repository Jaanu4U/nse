from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.routers.auth import get_optional_user, UserResponse
from app.repositories.user_repo import UserRepository
from app.repositories.stock_repo import StockRepository
from app.services.portfolio import PortfolioService
from pydantic import BaseModel
import datetime
from typing import List, Optional

router = APIRouter(tags=["Portfolios & Watchlists"])

# Request/Response schemas
class NameRequest(BaseModel):
    name: str

class TransactionRequest(BaseModel):
    symbol: str
    transaction_type: str # 'BUY' or 'SELL'
    quantity: int
    price: float
    transaction_date: datetime.date
    charges: Optional[float] = 0.0

@router.get("/watchlists")
def get_watchlists(current_user: UserResponse = Depends(get_optional_user), db: Session = Depends(get_db)):
    repo = UserRepository(db)
    return repo.get_watchlists_by_user(current_user.id)

@router.post("/watchlists")
def create_watchlist(req: NameRequest, current_user: UserResponse = Depends(get_optional_user), db: Session = Depends(get_db)):
    repo = UserRepository(db)
    return repo.create_watchlist(current_user.id, req.name)

@router.post("/watchlists/{watchlist_id}/items/{symbol}")
def add_to_watchlist(watchlist_id: int, symbol: str, current_user: UserResponse = Depends(get_optional_user), db: Session = Depends(get_db)):
    user_repo = UserRepository(db)
    stock_repo = StockRepository(db)
    
    # Check if stock exists
    stock = stock_repo.get_by_symbol(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")
        
    user_repo.add_to_watchlist(watchlist_id, stock.id)
    return {"status": "success", "message": f"Added {symbol} to watchlist"}

@router.delete("/watchlists/{watchlist_id}/items/{symbol}")
def remove_from_watchlist(watchlist_id: int, symbol: str, current_user: UserResponse = Depends(get_optional_user), db: Session = Depends(get_db)):
    user_repo = UserRepository(db)
    stock_repo = StockRepository(db)
    
    stock = stock_repo.get_by_symbol(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")
        
    user_repo.remove_from_watchlist(watchlist_id, stock.id)
    return {"status": "success", "message": f"Removed {symbol} from watchlist"}

@router.get("/portfolios")
def get_portfolios(current_user: UserResponse = Depends(get_optional_user), db: Session = Depends(get_db)):
    repo = UserRepository(db)
    return repo.get_portfolios_by_user(current_user.id)

@router.post("/portfolios")
def create_portfolio(req: NameRequest, current_user: UserResponse = Depends(get_optional_user), db: Session = Depends(get_db)):
    repo = UserRepository(db)
    return repo.create_portfolio(current_user.id, req.name)

@router.get("/portfolios/{portfolio_id}")
def get_portfolio_summary(portfolio_id: int, current_user: UserResponse = Depends(get_optional_user), db: Session = Depends(get_db)):
    service = PortfolioService(db)
    return service.get_portfolio_summary(portfolio_id)

@router.post("/portfolios/{portfolio_id}/transactions")
def add_transaction(portfolio_id: int, tx: TransactionRequest, current_user: UserResponse = Depends(get_optional_user), db: Session = Depends(get_db)):
    user_repo = UserRepository(db)
    stock_repo = StockRepository(db)
    
    stock = stock_repo.get_by_symbol(tx.symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")
        
    new_tx = user_repo.add_transaction(
        portfolio_id=portfolio_id,
        stock_id=stock.id,
        tx_type=tx.transaction_type,
        qty=tx.quantity,
        price=tx.price,
        tx_date=tx.transaction_date,
        charges=tx.charges
    )
    return new_tx
