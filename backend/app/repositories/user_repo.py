from sqlalchemy.orm import Session
from app.models.models import User, Watchlist, Portfolio, Transaction, Stock, watchlist_items
from app.utils.security import get_password_hash
from typing import List, Optional
import datetime

class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_email(self, email: str) -> Optional[User]:
        return self.db.query(User).filter(User.email == email.lower()).first()

    def get_by_id(self, user_id: int) -> Optional[User]:
        return self.db.query(User).filter(User.id == user_id).first()

    def create(self, email: str, password_plain: str) -> User:
        hashed_password = get_password_hash(password_plain)
        user = User(email=email.lower(), hashed_password=hashed_password)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    # Watchlist Operations
    def get_watchlists_by_user(self, user_id: int) -> List[Watchlist]:
        return self.db.query(Watchlist).filter(Watchlist.user_id == user_id).all()

    def create_watchlist(self, user_id: int, name: str) -> Watchlist:
        wl = Watchlist(user_id=user_id, name=name)
        self.db.add(wl)
        self.db.commit()
        self.db.refresh(wl)
        return wl

    def add_to_watchlist(self, watchlist_id: int, stock_id: int) -> bool:
        # Check if already exists
        exists = self.db.query(watchlist_items).filter(
            watchlist_items.c.watchlist_id == watchlist_id,
            watchlist_items.c.stock_id == stock_id
        ).first()
        if exists:
            return True
            
        stmt = watchlist_items.insert().values(watchlist_id=watchlist_id, stock_id=stock_id)
        self.db.execute(stmt)
        self.db.commit()
        return True

    def remove_from_watchlist(self, watchlist_id: int, stock_id: int) -> bool:
        stmt = watchlist_items.delete().where(
            watchlist_items.c.watchlist_id == watchlist_id,
            watchlist_items.c.stock_id == stock_id
        )
        self.db.execute(stmt)
        self.db.commit()
        return True

    # Portfolio Operations
    def get_portfolios_by_user(self, user_id: int) -> List[Portfolio]:
        return self.db.query(Portfolio).filter(Portfolio.user_id == user_id).all()

    def create_portfolio(self, user_id: int, name: str) -> Portfolio:
        portfolio = Portfolio(user_id=user_id, name=name)
        self.db.add(portfolio)
        self.db.commit()
        self.db.refresh(portfolio)
        return portfolio

    def add_transaction(self, portfolio_id: int, stock_id: int, tx_type: str, qty: int, price: float, tx_date: datetime.date, charges: float = 0.0) -> Transaction:
        tx = Transaction(
            portfolio_id=portfolio_id,
            stock_id=stock_id,
            transaction_type=tx_type.upper(),
            quantity=qty,
            price=price,
            transaction_date=tx_date,
            charges=charges
        )
        self.db.add(tx)
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def get_portfolio_transactions(self, portfolio_id: int) -> List[Transaction]:
        return (
            self.db.query(Transaction)
            .filter(Transaction.portfolio_id == portfolio_id)
            .order_by(Transaction.transaction_date.asc())
            .all()
        )
