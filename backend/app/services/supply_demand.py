import pandas as pd
import numpy as np
import logging
from sqlalchemy.orm import Session
from app.repositories.price_repo import PriceRepository
from app.repositories.stock_repo import StockRepository
import datetime
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class SupplyDemandEngine:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)

    def _get_price_df(self, symbol: str, lookback_days: int = 250) -> Optional[pd.DataFrame]:
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return None
        
        start_date = datetime.date.today() - datetime.timedelta(days=lookback_days)
        prices = self.price_repo.get_daily_prices(stock.id, start_date, datetime.date.today())
        if len(prices) < 20:
            return None
            
        df = pd.DataFrame([{
            'date': p.timestamp,
            'open': float(p.open),
            'high': float(p.high),
            'low': float(p.low),
            'close': float(p.close),
            'volume': int(p.volume)
        } for p in prices])
        
        df.sort_values('date', inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def find_swings(self, df: pd.DataFrame, window: int = 5) -> Dict[str, List[Dict[str, Any]]]:
        """
        Locate swing highs and swing lows.
        A swing high/low is the highest/lowest price in a range of [i - window, i + window].
        """
        highs = []
        lows = []
        
        for i in range(window, len(df) - window):
            # Swing High
            is_high = True
            for w in range(1, window + 1):
                if df.loc[i, 'high'] < df.loc[i - w, 'high'] or df.loc[i, 'high'] < df.loc[i + w, 'high']:
                    is_high = False
                    break
            if is_high:
                highs.append({
                    "price": df.loc[i, 'high'],
                    "date": df.loc[i, 'date'],
                    "volume": df.loc[i, 'volume'],
                    "index": i
                })
                
            # Swing Low
            is_low = True
            for w in range(1, window + 1):
                if df.loc[i, 'low'] > df.loc[i - w, 'low'] or df.loc[i, 'low'] > df.loc[i + w, 'low']:
                    is_low = False
                    break
            if is_low:
                lows.append({
                    "price": df.loc[i, 'low'],
                    "date": df.loc[i, 'date'],
                    "volume": df.loc[i, 'volume'],
                    "index": i
                })
                
        return {"swing_highs": highs, "swing_lows": lows}

    def detect_support_resistance(self, symbol: str, lookback_days: int = 250, tolerance_pct: float = 1.5) -> Dict[str, List[Dict[str, Any]]]:
        """
        Analyze price levels and group swing points into Support and Resistance zones.
        """
        df = self._get_price_df(symbol, lookback_days)
        if df is None:
            return {"supports": [], "resistances": []}
            
        current_price = df.loc[len(df) - 1, 'close']
        
        # Find swing points
        swings = self.find_swings(df, window=5)
        all_points = swings["swing_highs"] + swings["swing_lows"]
        
        if not all_points:
            return {"supports": [], "resistances": []}
            
        # Group similar price points together within tolerance
        levels = []
        for pt in all_points:
            price = pt["price"]
            found = False
            
            # Check if this fits in an existing cluster level
            for lvl in levels:
                diff = abs(lvl["price"] - price) / lvl["price"] * 100
                if diff <= tolerance_pct:
                    lvl["points"].append(pt)
                    # Recalculate average price of this level
                    lvl["price"] = np.mean([p["price"] for p in lvl["points"]])
                    found = True
                    break
            
            if not found:
                levels.append({
                    "price": price,
                    "points": [pt]
                })

        # Calculate confidence scores for each level
        # Confidence score depends on:
        # 1. Number of touches (more points in cluster = higher)
        # 2. Recency (if it was touched recently = higher)
        # 3. Volume on rejection
        max_touches = max([len(lvl["points"]) for lvl in levels]) if levels else 1
        
        for lvl in levels:
            touches = len(lvl["points"])
            
            # Find recency (days ago)
            recency_penalty = 1.0
            newest_date = max([p["date"] for p in lvl["points"]])
            days_ago = (datetime.date.today() - newest_date).days
            if days_ago < 30:
                recency_penalty = 1.2
            elif days_ago > 180:
                recency_penalty = 0.8
                
            # Base touch score (scaled 1-10)
            touch_score = (touches / max_touches) * 8
            
            # Compute confidence out of 100
            confidence = min(100.0, (touch_score + 2.0) * recency_penalty * 10)
            lvl["confidence"] = round(confidence, 1)
            lvl["touches"] = touches
            lvl["last_touch"] = newest_date
            
            # Remove raw points list to avoid deep nesting in output
            del lvl["points"]
            
        # Separate levels into support (below current price) and resistance (above current price)
        supports = [lvl for lvl in levels if lvl["price"] < current_price]
        resistances = [lvl for lvl in levels if lvl["price"] > current_price]
        
        # Sort supports descending (closest first), resistances ascending (closest first)
        supports.sort(key=lambda x: x["price"], reverse=True)
        resistances.sort(key=lambda x: x["price"])
        
        return {
            "supports": supports,
            "resistances": resistances
        }

    def detect_breakouts(self, symbol: str) -> Dict[str, Any]:
        """
        Check if the current close price is breaking out of the nearest support/resistance zone.
        """
        df = self._get_price_df(symbol, lookback_days=250)
        if df is None:
            return {"breakout_detected": False}
            
        current_idx = len(df) - 1
        current_close = df.loc[current_idx, 'close']
        current_volume = df.loc[current_idx, 'volume']
        
        # 20-day volume average
        avg_volume = df.loc[max(0, current_idx-20):current_idx-1, 'volume'].mean()
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        # Get zones
        zones = self.detect_support_resistance(symbol, lookback_days=250)
        supports = zones["supports"]
        resistances = zones["resistances"]
        
        # Check Resistance Breakout (Close crosses above the closest resistance)
        if resistances:
            nearest_res = resistances[0]  # Closest resistance
            # If current close is within 2% above resistance, and volume is high
            if current_close > nearest_res["price"] and (current_close / nearest_res["price"] - 1.0) < 0.03:
                if volume_ratio > 1.3:
                    return {
                        "breakout_detected": True,
                        "type": "BULLISH_BREAKOUT",
                        "level": nearest_res["price"],
                        "confidence": min(100, int(nearest_res["confidence"] * 0.7 + volume_ratio * 15)),
                        "volume_ratio": round(volume_ratio, 2)
                    }

        # Check Support Breakdown (Close crosses below the closest support)
        if supports:
            nearest_sup = supports[0]  # Closest support
            if current_close < nearest_sup["price"] and (1.0 - current_close / nearest_sup["price"]) < 0.03:
                if volume_ratio > 1.3:
                    return {
                        "breakout_detected": True,
                        "type": "BEARISH_BREAKOUT",
                        "level": nearest_sup["price"],
                        "confidence": min(100, int(nearest_sup["confidence"] * 0.7 + volume_ratio * 15)),
                        "volume_ratio": round(volume_ratio, 2)
                    }

        return {
            "breakout_detected": False,
            "volume_ratio": round(volume_ratio, 2)
        }
