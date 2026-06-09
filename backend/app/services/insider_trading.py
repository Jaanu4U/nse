import datetime
import random
import logging
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from app.models.models import Stock, InsiderTrade
from app.repositories.stock_repo import StockRepository
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

NAMES = [
    "Promoter Group Trust", "Executive Director Holdings", "KMP Capital Trust",
    "Chairman Private Fund", "Managing Director Pension Plan", "Promoter Core Trust"
]

CATEGORIES = ["Promoter Group", "Director", "Key Managerial Personnel (KMP)"]

class InsiderTradingService:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)

    def sync_insider_trades(self, symbol: str) -> int:
        """
        Download/simulate insider trading activities for a given stock symbol.
        In a real application, crawls BSE/NSE corporate disclosures.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            logger.error(f"Stock {symbol} not found.")
            return 0

        # Check existing trades count
        existing_count = self.db.query(func.count(InsiderTrade.id)).filter(InsiderTrade.stock_id == stock.id).scalar()
        if existing_count > 0:
            # Already seeded or synced, skip unless we want to add a new one randomly
            if random.random() > 0.8:
                self._seed_single_trade(stock.id, datetime.date.today())
                self.db.commit()
                return 1
            return 0

        # Seeding a historical history of trades over the last 90 days
        count = 0
        today = datetime.date.today()
        for i in range(1, 90):
            # Only trigger some dates randomly to look realistic
            if random.random() > 0.85:
                date = today - datetime.timedelta(days=i)
                self._seed_single_trade(stock.id, date)
                count += 1

        self.db.commit()
        logger.info(f"Synced {count} promoter trades for {symbol}.")
        return count

    def _seed_single_trade(self, stock_id: int, date: datetime.date):
        acquirer = random.choice(NAMES)
        category = random.choice(CATEGORIES)
        tx_type = "BUY" if random.random() > 0.4 else "SELL"  # Promoters usually buy
        qty = random.randint(1000, 150000)
        # Random price range around typical stock price, let's say average is 1500
        price = random.uniform(500, 3000)
        val = qty * price

        trade = InsiderTrade(
            stock_id=stock_id,
            acquirer_name=acquirer,
            category=category,
            transaction_type=tx_type,
            quantity=qty,
            value=round(val, 2),
            transaction_date=date
        )
        self.db.add(trade)

    def get_stock_insider_trades(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """
        Retrieve trades for a symbol, including net stats over the last 90 days.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return {"trades": [], "summary": {}}

        trades = self.db.query(InsiderTrade).filter(
            InsiderTrade.stock_id == stock.id
        ).order_by(desc(InsiderTrade.transaction_date)).limit(limit).all()

        # Compute summary over last 90 days
        cutoff = datetime.date.today() - datetime.timedelta(days=90)
        recent_trades = self.db.query(InsiderTrade).filter(
            InsiderTrade.stock_id == stock.id,
            InsiderTrade.transaction_date >= cutoff
        ).all()

        total_bought = 0.0
        total_sold = 0.0
        for t in recent_trades:
            val = float(t.value)
            if t.transaction_type == "BUY":
                total_bought += val
            else:
                total_sold += val

        net_flow = total_bought - total_sold
        signal = "NEUTRAL"
        if net_flow > 10000000:  # > 1 Crore net buying
            signal = "BULLISH_ACCUMULATION"
        elif net_flow < -10000000:  # > 1 Crore net selling
            signal = "BEARISH_LIQUIDATION"

        formatted_list = []
        for t in trades:
            formatted_list.append({
                "acquirer": t.acquirer_name,
                "category": t.category,
                "type": t.transaction_type,
                "quantity": int(t.quantity),
                "value": float(t.value),
                "date": t.transaction_date.strftime("%Y-%m-%d")
            })

        return {
            "trades": formatted_list,
            "summary": {
                "total_bought_90d": round(total_bought, 2),
                "total_sold_90d": round(total_sold, 2),
                "net_flow_90d": round(net_flow, 2),
                "signal": signal
            }
        }
