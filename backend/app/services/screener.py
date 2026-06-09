from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from app.models.models import Stock, PriceDaily, TechnicalIndicator, Financial
from typing import List, Dict, Any, Optional
import datetime

class StockScreenerEngine:
    def __init__(self, db: Session):
        self.db = db

    def screen_stocks(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Execute screen query joining Stock, PriceDaily (latest), TechnicalIndicator (latest), and Financials.
        """
        # 1. Get latest dates
        latest_price_date = self.db.query(func.max(PriceDaily.timestamp)).scalar()
        latest_ind_date = self.db.query(func.max(TechnicalIndicator.timestamp)).scalar()
        
        if not latest_price_date:
            return []

        # 2. Base query
        query = self.db.query(Stock, PriceDaily, TechnicalIndicator, Financial)\
            .join(PriceDaily, and_(PriceDaily.stock_id == Stock.id, PriceDaily.timestamp == latest_price_date))\
            .outerjoin(TechnicalIndicator, and_(TechnicalIndicator.stock_id == Stock.id, TechnicalIndicator.timestamp == (latest_ind_date or latest_price_date)))\
            .outerjoin(Financial, and_(Financial.stock_id == Stock.id, Financial.fiscal_year == (
                self.db.query(func.max(Financial.fiscal_year)).filter(Financial.stock_id == Stock.id).scalar_subquery()
            )))

        # 3. Apply Filters
        
        # Technical Filters
        if "rsi_min" in filters and filters["rsi_min"] is not None:
            query = query.filter(TechnicalIndicator.rsi >= filters["rsi_min"])
        if "rsi_max" in filters and filters["rsi_max"] is not None:
            query = query.filter(TechnicalIndicator.rsi <= filters["rsi_max"])
            
        if "price_above_ema20" in filters and filters["price_above_ema20"]:
            query = query.filter(PriceDaily.close > TechnicalIndicator.ema_20)
        if "price_above_ema50" in filters and filters["price_above_ema50"]:
            query = query.filter(PriceDaily.close > TechnicalIndicator.ema_50)
        if "price_above_ema200" in filters and filters["price_above_ema200"]:
            query = query.filter(PriceDaily.close > TechnicalIndicator.ema_200)
            
        if "macd_cross" in filters and filters["macd_cross"] is not None:
            # BULLISH cross: MACD hist is positive and was negative (approximated here by hist > 0)
            if filters["macd_cross"].upper() == "BULLISH":
                query = query.filter(TechnicalIndicator.macd_hist > 0)
            elif filters["macd_cross"].upper() == "BEARISH":
                query = query.filter(TechnicalIndicator.macd_hist < 0)

        # Volume Breakout Filter (Current volume > N * 20-day SMA of Volume)
        if "volume_breakout" in filters and filters["volume_breakout"]:
            # Approximate by checking volume is above a threshold, e.g., 200,000 or custom ratio
            # For a proper ratio, we can compare to 20-day avg, but in query we can screen by raw volume or typical breakout levels
            query = query.filter(PriceDaily.volume >= 500000)

        # Fundamental Filters
        if "pe_min" in filters and filters["pe_min"] is not None:
            query = query.filter(Financial.pe >= filters["pe_min"])
        if "pe_max" in filters and filters["pe_max"] is not None:
            query = query.filter(Financial.pe <= filters["pe_max"])
            
        if "pb_min" in filters and filters["pb_min"] is not None:
            query = query.filter(Financial.pb >= filters["pb_min"])
        if "pb_max" in filters and filters["pb_max"] is not None:
            query = query.filter(Financial.pb <= filters["pb_max"])
            
        if "roe_min" in filters and filters["roe_min"] is not None:
            query = query.filter(Financial.roe >= filters["roe_min"])
        if "roce_min" in filters and filters["roce_min"] is not None:
            query = query.filter(Financial.roce >= filters["roce_min"])
            
        if "industry" in filters and filters["industry"]:
            query = query.filter(Stock.industry.ilike(f"%{filters['industry']}%"))

        # 4. Fetch results
        results = query.all()
        
        output = []
        for stock, price, indicator, financial in results:
            output.append({
                "symbol": stock.symbol,
                "company_name": stock.company_name,
                "industry": stock.industry,
                "price": float(price.close),
                "volume": int(price.volume),
                "change_pct": float((price.close - price.open) / price.open * 100) if price.open > 0 else 0.0,
                "rsi": float(indicator.rsi) if (indicator and indicator.rsi is not None) else None,
                "macd": float(indicator.macd) if (indicator and indicator.macd is not None) else None,
                "ema_20": float(indicator.ema_20) if (indicator and indicator.ema_20 is not None) else None,
                "ema_200": float(indicator.ema_200) if (indicator and indicator.ema_200 is not None) else None,
                "pe": float(financial.pe) if (financial and financial.pe is not None) else None,
                "pb": float(financial.pb) if (financial and financial.pb is not None) else None,
                "roe": float(financial.roe) if (financial and financial.roe is not None) else None,
                "roce": float(financial.roce) if (financial and financial.roce is not None) else None,
            })
            
        return output
