import pandas as pd
import numpy as np
import logging
from sqlalchemy.orm import Session
from app.repositories.price_repo import PriceRepository
from app.repositories.stock_repo import StockRepository
from app.models.models import DetectedPattern
from app.services.supply_demand import SupplyDemandEngine
import datetime
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class PatternRecognitionEngine:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)
        self.sd_engine = SupplyDemandEngine(db)

    def detect_engulfing_patterns(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Detect Bullish and Bearish Engulfing patterns.
        Requires OHLC columns. Returns pattern records for the last 5 days.
        """
        patterns = []
        if len(df) < 5:
            return patterns

        # Check last 5 candles for patterns
        for i in range(len(df) - 5, len(df)):
            if i < 1:
                continue
            
            # Prev candle
            open_p = df.loc[i - 1, 'open']
            close_p = df.loc[i - 1, 'close']
            
            # Curr candle
            open_c = df.loc[i, 'open']
            close_c = df.loc[i, 'close']
            
            # 20-day close standard deviation for typical candle height
            close_std = df.loc[max(0, i-20):i, 'close'].std()
            body_c = abs(close_c - open_c)
            
            # Skip noise (very small candles)
            if body_c < (close_std * 0.1):
                continue
                
            # Bullish Engulfing: Red body followed by larger Green body
            if close_p < open_p and close_c > open_c:
                if open_c <= close_p and close_c >= open_p:
                    patterns.append({
                        "pattern_name": "BULLISH_ENGULFING",
                        "signal_type": "BULLISH",
                        "timestamp": df.loc[i, 'date'],
                        "confidence": 80.0
                    })
                    
            # Bearish Engulfing: Green body followed by larger Red body
            elif close_p > open_p and close_c < open_c:
                if open_c >= close_p and close_c <= open_p:
                    patterns.append({
                        "pattern_name": "BEARISH_ENGULFING",
                        "signal_type": "BEARISH",
                        "timestamp": df.loc[i, 'date'],
                        "confidence": 80.0
                    })
                    
        return patterns

    def detect_double_top_bottom(self, df: pd.DataFrame, swings: Dict[str, List[Dict[str, Any]]], tolerance_pct: float = 1.5) -> List[Dict[str, Any]]:
        """
        Detect Double Top and Double Bottom configurations.
        """
        patterns = []
        highs = swings["swing_highs"]
        lows = swings["swing_lows"]
        
        # Double Top: Two consecutive highs at similar price level with a trough in between
        if len(highs) >= 2:
            h1, h2 = highs[-2], highs[-1]
            diff = abs(h1["price"] - h2["price"]) / h1["price"] * 100
            # Ensure the second top happened recently (last 10 days) and they are spaced apart
            if diff <= tolerance_pct and (h2["index"] - h1["index"]) > 5:
                # Find the minimum close in between the two tops (the neck line)
                neck_df = df.loc[h1["index"]:h2["index"]]
                if not neck_df.empty:
                    min_val = neck_df['close'].min()
                    # Current price must be near neck line or starting to break down
                    curr_close = df.iloc[-1]['close']
                    if curr_close < h2["price"] and curr_close > min_val * 0.95:
                        patterns.append({
                            "pattern_name": "DOUBLE_TOP",
                            "signal_type": "BEARISH",
                            "timestamp": df.iloc[-1]['date'],
                            "confidence": round(100 - diff * 10, 1)
                        })

        # Double Bottom: Two consecutive lows at similar level with a peak in between
        if len(lows) >= 2:
            l1, l2 = lows[-2], lows[-1]
            diff = abs(l1["price"] - l2["price"]) / l1["price"] * 100
            if diff <= tolerance_pct and (l2["index"] - l1["index"]) > 5:
                # Find maximum close in between
                neck_df = df.loc[l1["index"]:l2["index"]]
                if not neck_df.empty:
                    max_val = neck_df['close'].max()
                    curr_close = df.iloc[-1]['close']
                    if curr_close > l2["price"] and curr_close < max_val * 1.05:
                        patterns.append({
                            "pattern_name": "DOUBLE_BOTTOM",
                            "signal_type": "BULLISH",
                            "timestamp": df.iloc[-1]['date'],
                            "confidence": round(100 - diff * 10, 1)
                        })
                        
        return patterns

    def detect_head_and_shoulders(self, df: pd.DataFrame, swings: Dict[str, List[Dict[str, Any]]], tolerance_pct: float = 2.0) -> List[Dict[str, Any]]:
        """
        Detect Head and Shoulders (H&S) and Inverse Head and Shoulders.
        """
        patterns = []
        highs = swings["swing_highs"]
        lows = swings["swing_lows"]
        
        # Head & Shoulders: Needs 3 highs (L_shoulder, Head, R_shoulder)
        # Head is highest; Left and Right shoulders are lower and roughly equal
        if len(highs) >= 3:
            s1, head, s2 = highs[-3], highs[-2], highs[-1]
            if head["price"] > s1["price"] and head["price"] > s2["price"]:
                shoulder_diff = abs(s1["price"] - s2["price"]) / s1["price"] * 100
                if shoulder_diff <= tolerance_pct:
                    patterns.append({
                        "pattern_name": "HEAD_AND_SHOULDERS",
                        "signal_type": "BEARISH",
                        "timestamp": df.iloc[-1]['date'],
                        "confidence": round(95 - shoulder_diff * 5, 1)
                    })

        # Inverse Head & Shoulders
        if len(lows) >= 3:
            s1, head, s2 = lows[-3], lows[-2], lows[-1]
            if head["price"] < s1["price"] and head["price"] < s2["price"]:
                shoulder_diff = abs(s1["price"] - s2["price"]) / s1["price"] * 100
                if shoulder_diff <= tolerance_pct:
                    patterns.append({
                        "pattern_name": "INVERSE_HEAD_AND_SHOULDERS",
                        "signal_type": "BULLISH",
                        "timestamp": df.iloc[-1]['date'],
                        "confidence": round(95 - shoulder_diff * 5, 1)
                    })

        return patterns

    def run_detection_for_stock(self, symbol: str) -> int:
        """
        Run all pattern detectors for a stock and persist results.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return 0
            
        df = self.sd_engine._get_price_df(symbol, lookback_days=250)
        if df is None or df.empty:
            return 0
            
        # Get swing pivots
        swings = self.sd_engine.find_swings(df, window=5)
        
        # Accumulate pattern candidates
        detected = []
        detected.extend(self.detect_engulfing_patterns(df))
        detected.extend(self.detect_double_top_bottom(df, swings))
        detected.extend(self.detect_head_and_shoulders(df, swings))
        
        # Persist detected patterns
        saved_count = 0
        for pat in detected:
            # Check if this pattern is already recorded for this stock and day to avoid duplicates
            exists = self.db.query(DetectedPattern).filter(
                DetectedPattern.stock_id == stock.id,
                DetectedPattern.timestamp == pat["timestamp"],
                DetectedPattern.pattern_name == pat["pattern_name"]
            ).first()
            
            if not exists:
                new_pat = DetectedPattern(
                    stock_id=stock.id,
                    timestamp=pat["timestamp"],
                    pattern_name=pat["pattern_name"],
                    signal_type=pat["signal_type"],
                    confidence=pat["confidence"]
                )
                self.db.add(new_pat)
                saved_count += 1
                
        if saved_count > 0:
            self.db.commit()
            logger.info(f"Saved {saved_count} detected patterns for {symbol}.")
            
        return saved_count

    def run_all_stocks(self) -> int:
        active_stocks = self.stock_repo.get_active_stocks()
        total = 0
        for stock in active_stocks:
            try:
                total += self.run_detection_for_stock(stock.symbol)
            except Exception as e:
                logger.error(f"Pattern detection failed for {stock.symbol}: {e}")
        return total
