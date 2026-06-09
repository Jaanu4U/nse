from sqlalchemy.orm import Session
from app.repositories.user_repo import UserRepository
from app.repositories.price_repo import PriceRepository
from typing import Dict, Any, List

class PortfolioService:
    def __init__(self, db: Session):
        self.db = db
        self.user_repo = UserRepository(db)
        self.price_repo = PriceRepository(db)

    def get_portfolio_summary(self, portfolio_id: int) -> Dict[str, Any]:
        """
        Process transactions chronologically to build holdings, calculate average buy price,
        realized P&L, current market values, and asset allocation percentages.
        """
        transactions = self.user_repo.get_portfolio_transactions(portfolio_id)
        
        # Build holdings
        holdings = {} # stock_id -> {symbol, company_name, quantity, avg_buy_price, realized_pnl}
        
        for tx in transactions:
            stock = tx.stock
            sid = stock.id
            symbol = stock.symbol
            
            if sid not in holdings:
                holdings[sid] = {
                    "symbol": symbol,
                    "company_name": stock.company_name,
                    "quantity": 0,
                    "avg_buy_price": 0.0,
                    "realized_pnl": 0.0,
                    "charges": 0.0
                }
                
            h = holdings[sid]
            h["charges"] += float(tx.charges)
            
            qty = tx.quantity
            price = float(tx.price)
            
            if tx.transaction_type == "BUY":
                current_qty = h["quantity"]
                current_cost = current_qty * h["avg_buy_price"]
                new_cost = qty * price
                
                total_qty = current_qty + qty
                if total_qty > 0:
                    h["avg_buy_price"] = (current_cost + new_cost) / total_qty
                h["quantity"] = total_qty
                
            elif tx.transaction_type == "SELL":
                current_qty = h["quantity"]
                if current_qty > 0:
                    sell_qty = min(current_qty, qty)
                    # Realized P&L = qty * (sell_price - avg_buy_price)
                    gain = sell_qty * (price - h["avg_buy_price"])
                    h["realized_pnl"] += gain
                    h["quantity"] = current_qty - sell_qty
                    
                    if h["quantity"] == 0:
                        h["avg_buy_price"] = 0.0
                        
        # Now fetch current prices for active holdings and compute unrealized metrics
        active_holdings = []
        total_invested = 0.0
        total_current_value = 0.0
        total_realized_pnl = 0.0
        total_charges = 0.0
        
        for sid, h in holdings.items():
            total_realized_pnl += h["realized_pnl"]
            total_charges += h["charges"]
            
            if h["quantity"] > 0:
                # Get latest price
                latest = self.price_repo.get_latest_daily_price(sid)
                current_price = float(latest.close) if latest else h["avg_buy_price"]
                
                qty = h["quantity"]
                avg_buy = h["avg_buy_price"]
                invested = qty * avg_buy
                current_val = qty * current_price
                unrealized = current_val - invested
                unrealized_pct = (unrealized / invested * 100) if invested > 0 else 0.0
                
                total_invested += invested
                total_current_value += current_val
                
                active_holdings.append({
                    "stock_id": sid,
                    "symbol": h["symbol"],
                    "company_name": h["company_name"],
                    "quantity": qty,
                    "avg_buy_price": round(avg_buy, 2),
                    "current_price": round(current_price, 2),
                    "invested_value": round(invested, 2),
                    "current_value": round(current_val, 2),
                    "unrealized_pnl": round(unrealized, 2),
                    "unrealized_pnl_pct": round(unrealized_pct, 2),
                    "realized_pnl": round(h["realized_pnl"], 2),
                    "allocation_pct": 0.0 # Will calculate in next pass
                })
            elif h["realized_pnl"] != 0:
                # Stock is fully exited, but we keep it for realized statistics
                active_holdings.append({
                    "stock_id": sid,
                    "symbol": h["symbol"],
                    "company_name": h["company_name"],
                    "quantity": 0,
                    "avg_buy_price": 0.0,
                    "current_price": 0.0,
                    "invested_value": 0.0,
                    "current_value": 0.0,
                    "unrealized_pnl": 0.0,
                    "unrealized_pnl_pct": 0.0,
                    "realized_pnl": round(h["realized_pnl"], 2),
                    "allocation_pct": 0.0
                })

        # Calculate allocation percentage
        for ah in active_holdings:
            if total_current_value > 0 and ah["quantity"] > 0:
                ah["allocation_pct"] = round((ah["current_value"] / total_current_value) * 100, 2)

        total_unrealized_pnl = total_current_value - total_invested
        total_unrealized_pct = (total_unrealized_pnl / total_invested * 100) if total_invested > 0 else 0.0

        return {
            "summary": {
                "total_invested": round(total_invested, 2),
                "total_current_value": round(total_current_value, 2),
                "total_unrealized_pnl": round(total_unrealized_pnl, 2),
                "total_unrealized_pnl_pct": round(total_unrealized_pct, 2),
                "total_realized_pnl": round(total_realized_pnl, 2),
                "total_charges": round(total_charges, 2),
                "net_pnl": round(total_unrealized_pnl + total_realized_pnl - total_charges, 2)
            },
            "holdings": active_holdings
        }
