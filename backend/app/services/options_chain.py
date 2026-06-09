import math
import random
import logging
from sqlalchemy.orm import Session
from app.repositories.stock_repo import StockRepository
from app.repositories.price_repo import PriceRepository
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class OptionsChainService:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)

    def get_options_chain(self, symbol: str) -> Dict[str, Any]:
        """
        Builds a comprehensive Options Chain for a stock.
        Calculates Put-Call Ratio (PCR), Max Pain, and dynamic strike levels.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return {"strikes": [], "pcr": 1.0, "max_pain": 0.0, "underlying_price": 0.0}

        latest_price = self.price_repo.get_latest_daily_price(stock.id)
        if not latest_price:
            return {"strikes": [], "pcr": 1.0, "max_pain": 0.0, "underlying_price": 0.0}

        underlying_price = float(latest_price.close)
        
        # 1. Determine Strike Interval based on stock price
        if underlying_price < 100:
            strike_interval = 2.5
        elif underlying_price < 500:
            strike_interval = 5.0
        elif underlying_price < 1000:
            strike_interval = 10.0
        elif underlying_price < 3000:
            strike_interval = 20.0
        else:
            strike_interval = 50.0

        # Round underlying to nearest strike interval to find ATM (At-The-Money)
        atm_strike = round(underlying_price / strike_interval) * strike_interval

        # Generate 7 strikes below and 7 strikes above ATM
        strikes = []
        for i in range(-7, 8):
            strikes.append(atm_strike + i * strike_interval)

        # 2. Simulate Option Chain metrics (OI, Volume, LTP, Greeks)
        # In a real system, this pulls from live WebSockets or NSE Option Chain API.
        option_rows = []
        total_call_oi = 0
        total_put_oi = 0
        
        for strike in strikes:
            # Distance from underlying price
            dist = strike - underlying_price
            
            # Simple model for Call & Put prices and Open Interest
            # Call OI is higher at resistance strikes; Put OI is higher at support strikes.
            # Implied volatility baseline
            iv = 0.18 + (abs(dist) / underlying_price) * 0.1  # Volatility smile
            
            # LTP pricing approximation using simulated Black-Scholes characteristics
            # Call price decreases as strike increases
            call_ltp = max(0.5, underlying_price * 0.04 * math.exp(-dist / (underlying_price * 0.08)))
            # Put price increases as strike increases
            put_ltp = max(0.5, underlying_price * 0.04 * math.exp(dist / (underlying_price * 0.08)))

            # Adjust LTP for intrinsic value
            if dist < 0: # Call is ITM
                call_ltp = max(call_ltp, abs(dist))
            else: # Put is ITM
                put_ltp = max(put_ltp, abs(dist))

            # Simulate Open Interest (OI)
            # Higher OI near ATM, and at round numbers
            multiplier = 2.5 if strike % (strike_interval * 2) == 0 else 1.0
            call_oi = int(max(1000, 100000 * math.exp(-abs(dist) / (underlying_price * 0.05)) * multiplier))
            put_oi = int(max(1000, 100000 * math.exp(-abs(dist) / (underlying_price * 0.05)) * multiplier))

            # Greeks calculation (rough approximation)
            # Delta ranges from 0 to 1 for Call, -1 to 0 for Put
            call_delta = 1.0 / (1.0 + math.exp(dist / (underlying_price * 0.05)))
            put_delta = call_delta - 1.0
            
            # Gamma is a Gaussian curve peaked at ATM
            gamma = math.exp(-0.5 * (dist / (underlying_price * 0.05))**2) / (underlying_price * iv * math.sqrt(2 * math.pi))

            total_call_oi += call_oi
            total_put_oi += put_oi

            option_rows.append({
                "strike": strike,
                "call": {
                    "ltp": round(call_ltp, 2),
                    "oi": call_oi,
                    "oi_change": int(call_oi * random.uniform(-0.15, 0.25)),
                    "volume": int(call_oi * random.uniform(1.2, 5.0)),
                    "iv": round(iv * 100, 2),
                    "delta": round(call_delta, 3),
                    "gamma": round(gamma, 5),
                },
                "put": {
                    "ltp": round(put_ltp, 2),
                    "oi": put_oi,
                    "oi_change": int(put_oi * random.uniform(-0.10, 0.30)),
                    "volume": int(put_oi * random.uniform(1.1, 4.5)),
                    "iv": round(iv * 100, 2),
                    "delta": round(put_delta, 3),
                    "gamma": round(gamma, 5),
                }
            })

        # 3. Calculate Put-Call Ratio (PCR)
        pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0

        # 4. Calculate Max Pain exactly
        # Loop through all strikes as potential settlement prices and find the strike
        # that minimizes the total value of options at expiration.
        min_pain_value = float('inf')
        max_pain_strike = atm_strike

        for test_strike in strikes:
            total_pain = 0.0
            for row in option_rows:
                strike_val = row["strike"]
                # For Call options: if market settles at test_strike, calls with strike_val < test_strike have value
                if test_strike > strike_val:
                    total_pain += (test_strike - strike_val) * row["call"]["oi"]
                # For Put options: if market settles at test_strike, puts with strike_val > test_strike have value
                if test_strike < strike_val:
                    total_pain += (strike_val - test_strike) * row["put"]["oi"]
            
            if total_pain < min_pain_value:
                min_pain_value = total_pain
                max_pain_strike = test_strike

        return {
            "underlying_price": round(underlying_price, 2),
            "pcr": round(pcr, 3),
            "max_pain": round(max_pain_strike, 2),
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "strikes": option_rows
        }
