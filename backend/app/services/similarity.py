import numpy as np
import pandas as pd
import logging
from sqlalchemy.orm import Session
from app.repositories.price_repo import PriceRepository
from app.repositories.indicator_repo import IndicatorRepository
from app.repositories.stock_repo import StockRepository
import datetime
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class SimilarityEngine:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)
        self.indicator_repo = IndicatorRepository(db)

    def find_similar_patterns(self, symbol: str, k: int = 5) -> Dict[str, Any]:
        """
        Given the current day's indicator values, find K most similar days in history.
        Compute win rate and expected next-day return based on historical outcomes.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return {"error": f"Stock {symbol} not found"}

        # Fetch last 3 years of daily prices and indicators
        start_date = datetime.date.today() - datetime.timedelta(days=365 * 3)
        prices = self.price_repo.get_daily_prices(stock.id, start_date, datetime.date.today())
        indicators = self.indicator_repo.get_indicators(stock.id, start_date, datetime.date.today())

        if len(prices) < 50:
            return {"win_rate": 50.0, "expected_return": 0.0, "matches": []}

        # Merge prices and indicators into a single dataset
        df_prices = pd.DataFrame([{
            'date': p.timestamp,
            'close': float(p.close),
            'volume': int(p.volume)
        } for p in prices]).set_index('date')

        df_ind = pd.DataFrame([{
            'date': i.timestamp,
            'rsi': float(i.rsi) if i.rsi is not None else np.nan,
            'macd_hist': float(i.macd_hist) if i.macd_hist is not None else np.nan,
            'ema_20': float(i.ema_20) if i.ema_20 is not None else np.nan,
            'ema_200': float(i.ema_200) if i.ema_200 is not None else np.nan,
            'atr': float(i.atr) if i.atr is not None else np.nan,
            'adx': float(i.adx) if i.adx is not None else np.nan,
        } for i in indicators]).set_index('date')

        df = df_prices.join(df_ind, how='inner').sort_index()

        # Compute additional features
        df['vol_sma'] = df['volume'].rolling(window=20).mean()
        df['volume_ratio'] = df['volume'] / df['vol_sma']
        df['dist_ema_20'] = (df['close'] - df['ema_20']) / df['ema_20']
        df['dist_ema_200'] = (df['close'] - df['ema_200']) / df['ema_200']
        
        # Target variable (next day's return)
        df['next_day_return'] = df['close'].pct_change().shift(-1)

        # Drop rows with NaN in features
        feature_cols = ['rsi', 'macd_hist', 'volume_ratio', 'dist_ema_20', 'dist_ema_200', 'adx']
        df = df.dropna(subset=feature_cols + ['next_day_return'])

        if len(df) < 30:
            return {"win_rate": 50.0, "expected_return": 0.0, "matches": []}

        # Separate feature matrix
        matrix = df[feature_cols].values
        
        # Standardize features (Z-score normalization)
        mean = matrix.mean(axis=0)
        std = matrix.std(axis=0)
        std = np.where(std == 0, 1e-8, std) # Avoid division by zero
        matrix_norm = (matrix - mean) / std

        # Current state is the very last row
        current_vector = matrix_norm[-1]
        
        # Compare with historical states (excluding the last 5 days to avoid looking at the immediate past)
        history_matrix = matrix_norm[:-5]
        history_dates = df.index[:-5]
        history_returns = df['next_day_return'].values[:-5]
        history_closes = df['close'].values[:-5]

        # Calculate Cosine Similarities
        # Cosine Similarity = A . B / (||A|| * ||B||)
        dot_products = np.dot(history_matrix, current_vector)
        norms_history = np.linalg.norm(history_matrix, axis=1)
        norm_current = np.linalg.norm(current_vector)
        
        # Handle zero norms
        norms_history = np.where(norms_history == 0, 1e-8, norms_history)
        norm_current = 1e-8 if norm_current == 0 else norm_current

        similarities = dot_products / (norms_history * norm_current)

        # Get top K indices of highest similarities
        top_k_indices = np.argsort(similarities)[-k:][::-1]

        matches = []
        positive_outcomes = 0
        returns_list = []

        for idx in top_k_indices:
            match_date = history_dates[idx]
            sim_score = float(similarities[idx])
            next_return = float(history_returns[idx])
            close_val = float(history_closes[idx])
            
            # Map score to percentage (0.0 - 1.0 to 0% - 100%)
            match_score = round((sim_score + 1) / 2 * 100, 1)

            matches.append({
                "date": match_date.strftime("%Y-%m-%d"),
                "similarity": match_score,
                "close": close_val,
                "next_day_return": round(next_return * 100, 2)
            })

            returns_list.append(next_return)
            if next_return > 0:
                positive_outcomes += 1

        win_rate = (positive_outcomes / k) * 100 if k > 0 else 50.0
        expected_return = float(np.mean(returns_list)) * 100 if returns_list else 0.0

        return {
            "win_rate": round(win_rate, 2),
            "expected_return": round(expected_return, 3),
            "matches": matches
        }
