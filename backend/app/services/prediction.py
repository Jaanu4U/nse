import os
import pickle
import logging
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from app.repositories.price_repo import PriceRepository
from app.repositories.indicator_repo import IndicatorRepository
from app.repositories.stock_repo import StockRepository
from app.models.models import Prediction
import xgboost as xgb
import datetime
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "models")
os.makedirs(MODEL_DIR, exist_ok=True)

class PredictionEngine:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)
        self.indicator_repo = IndicatorRepository(db)
        self._index_returns = None  # cached NIFTY benchmark daily returns (Series by date)

    def _get_index_returns(self) -> Optional[pd.Series]:
        """
        Load and cache the NIFTY 50 benchmark daily returns for relative-strength / beta features.
        Returns None if the index has not been seeded yet.
        """
        if self._index_returns is not None:
            return self._index_returns

        idx_stock = self.stock_repo.get_by_symbol("NIFTY50IDX")
        if not idx_stock:
            return None

        start_date = datetime.date.today() - datetime.timedelta(days=365 * 3)
        idx_prices = self.price_repo.get_daily_prices(idx_stock.id, start_date, datetime.date.today())
        if len(idx_prices) < 100:
            return None

        idx_df = pd.DataFrame([
            {"date": p.timestamp, "close": float(p.close)} for p in idx_prices
        ]).set_index("date").sort_index()
        self._index_returns = idx_df["close"].pct_change()
        return self._index_returns


    def _prepare_data(self, symbol: str) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """
        Fetch price and indicator data, merge them, and build model features.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return None, None

        # Fetch last 3 years of daily prices and indicators
        start_date = datetime.date.today() - datetime.timedelta(days=365 * 3)
        prices = self.price_repo.get_daily_prices(stock.id, start_date, datetime.date.today())
        indicators = self.indicator_repo.get_indicators(stock.id, start_date, datetime.date.today())
        
        if len(prices) < 100:
            return None, None

        # Load into dataframes
        df_prices = pd.DataFrame([{
            'date': p.timestamp,
            'open': float(p.open),
            'high': float(p.high),
            'low': float(p.low),
            'close': float(p.close),
            'volume': int(p.volume)
        } for p in prices]).set_index('date').sort_index()

        df_ind = pd.DataFrame([{
            'date': i.timestamp,
            'rsi': float(i.rsi) if i.rsi is not None else np.nan,
            'macd_hist': float(i.macd_hist) if i.macd_hist is not None else np.nan,
            'ema_20': float(i.ema_20) if i.ema_20 is not None else np.nan,
            'ema_50': float(i.ema_50) if i.ema_50 is not None else np.nan,
            'ema_200': float(i.ema_200) if i.ema_200 is not None else np.nan,
            'atr': float(i.atr) if i.atr is not None else np.nan,
            'adx': float(i.adx) if i.adx is not None else np.nan,
            'stoch_k': float(i.stoch_k) if i.stoch_k is not None else np.nan,
            'mfi': float(i.mfi) if i.mfi is not None else np.nan,
            'cci': float(i.cci) if i.cci is not None else np.nan,
            'williams_r': float(i.williams_r) if i.williams_r is not None else np.nan,
            'supertrend_dir': float(i.supertrend_dir) if i.supertrend_dir is not None else np.nan,
        } for i in indicators]).set_index('date').sort_index()

        # Merge dataframes
        df = df_prices.join(df_ind, how='inner')
        if len(df) < 50:
            return None, None

        # Calculate features
        df['return_1d'] = df['close'].pct_change(1)
        df['return_3d'] = df['close'].pct_change(3)
        df['return_5d'] = df['close'].pct_change(5)
        
        # Volume ratio vs 20-day SMA volume
        df['vol_sma_20'] = df['volume'].rolling(window=20).mean()
        df['volume_ratio'] = df['volume'] / df['vol_sma_20']
        
        # Distance to EMA
        df['dist_ema_20'] = (df['close'] - df['ema_20']) / df['ema_20']
        df['dist_ema_50'] = (df['close'] - df['ema_50']) / df['ema_50']
        df['dist_ema_200'] = (df['close'] - df['ema_200']) / df['ema_200']
        
        # Normalized ATR
        df['atr_ratio'] = df['atr'] / df['close']

        # Rate of change (momentum) at multiple horizons
        df['roc_10'] = df['close'].pct_change(10)
        df['roc_20'] = df['close'].pct_change(20)

        # Realized volatility: std of daily log returns over 20 sessions
        log_ret = np.log(df['close'] / df['close'].shift(1))
        df['realized_vol_20'] = log_ret.rolling(window=20).std()

        # Distance from rolling 52-week (252-session) high / low
        roll_high_252 = df['close'].rolling(window=252, min_periods=60).max()
        roll_low_252 = df['close'].rolling(window=252, min_periods=60).min()
        df['dist_52w_high'] = (df['close'] - roll_high_252) / roll_high_252
        df['dist_52w_low'] = (df['close'] - roll_low_252) / roll_low_252

        # Relative strength & beta vs the NIFTY benchmark
        idx_ret = self._get_index_returns()
        if idx_ret is not None:
            stock_ret_1d = df['close'].pct_change()
            aligned_idx = idx_ret.reindex(df.index)
            # 20-day relative strength: stock cumulative return minus index cumulative return
            df['rs_nifty_20'] = df['close'].pct_change(20) - (aligned_idx.add(1).rolling(20).apply(np.prod, raw=True) - 1)
            # Rolling 60-day beta = cov(stock, index) / var(index)
            cov = stock_ret_1d.rolling(60).cov(aligned_idx)
            var = aligned_idx.rolling(60).var()
            df['beta_60'] = cov / var.replace(0, np.nan)
        else:
            df['rs_nifty_20'] = 0.0
            df['beta_60'] = 1.0

        # Next-day return (prediction target)
        df['next_close'] = df['close'].shift(-1)
        df['next_day_return'] = (df['next_close'] - df['close']) / df['close']

        # Clean NaNs
        df = df.dropna(subset=['rsi', 'macd_hist', 'volume_ratio', 'dist_ema_20', 'atr_ratio', 'adx'])
        # Fill the newer feature columns (early-history NaNs) with neutral values so rows are retained.
        fill_defaults = {
            'stoch_k': 50.0, 'mfi': 50.0, 'cci': 0.0, 'williams_r': -50.0, 'supertrend_dir': 0.0,
            'roc_10': 0.0, 'roc_20': 0.0, 'realized_vol_20': 0.0,
            'dist_52w_high': 0.0, 'dist_52w_low': 0.0, 'rs_nifty_20': 0.0, 'beta_60': 1.0,
        }
        df = df.fillna(value=fill_defaults)

        # Split into features (X) and future target (y)
        feature_cols = [
            'rsi', 'macd_hist', 'volume_ratio', 'dist_ema_20', 
            'dist_ema_50', 'dist_ema_200', 'atr_ratio', 'adx', 
            'return_1d', 'return_3d', 'return_5d',
            'stoch_k', 'mfi', 'cci', 'williams_r', 'supertrend_dir',
            'roc_10', 'roc_20', 'realized_vol_20',
            'dist_52w_high', 'dist_52w_low', 'rs_nifty_20', 'beta_60',
        ]
        
        X = df[feature_cols].copy()
        y = df[['next_day_return']].copy()
        
        return X, y

    def train_models(self, symbol: str) -> bool:
        """
        Train binary XGBoost classifiers for each target threshold (+1%, +2%, +3%, +5%).
        """
        X, y = self._prepare_data(symbol)
        if X is None or len(X) < 100:
            logger.warning(f"Insufficient historical data to train model for {symbol}.")
            return False

        # Drop the last row of X since it doesn't have a next_day_return target (shifted)
        X_train = X.iloc[:-1]
        y_train_raw = y.iloc[:-1]['next_day_return']

        thresholds = [0.01, 0.02, 0.03, 0.05]
        models = {}

        for th in thresholds:
            y_bin = (y_train_raw >= th).astype(int)
            n_pos = int(y_bin.sum())

            # Degenerate target: a single class present means a classifier can't be trained.
            # Store the empirical prior (0.0 or 1.0) instead so prediction stays consistent.
            if n_pos == 0:
                models[th] = 0.0
                continue
            if n_pos == len(y_bin):
                models[th] = 1.0
                continue

            # Regularized model. Larger thresholds have rare positive classes, so subsampling,
            # min_child_weight and L2 regularization curb the severe overfitting that previously
            # produced inflated probabilities for big-move (>=3%, >=5%) targets.
            model = xgb.XGBClassifier(
                n_estimators=120,
                max_depth=3,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=5,
                reg_lambda=2.0,
                random_state=42,
                eval_metric="logloss"
            )
            model.fit(X_train, y_bin)
            models[th] = model

        # Save models to disk
        model_path = os.path.join(MODEL_DIR, f"{symbol}_models.pkl")
        with open(model_path, "wb") as f:
            pickle.dump(models, f)
            
        logger.info(f"Successfully trained next-day prediction models for {symbol}.")
        return True

    def predict_next_day(self, symbol: str) -> Dict[str, float]:
        """
        Predict probability scores of stock moving +1%, +2%, +3%, +5% tomorrow.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return {}

        X, y = self._prepare_data(symbol)
        
        # If we don't have enough data
        if X is None or X.empty:
            return self._get_fallback_probabilities(symbol)

        # We take the very last row (the current state)
        current_features = X.iloc[[-1]]
        current_date = X.index[-1]
        
        # Try loading models
        model_path = os.path.join(MODEL_DIR, f"{symbol}_models.pkl")
        
        # If not trained, try training now
        if not os.path.exists(model_path):
            success = self.train_models(symbol)
            if not success:
                return self._get_fallback_probabilities(symbol)

        try:
            with open(model_path, "rb") as f:
                models = pickle.load(f)
                
            probs = {}
            for th, model in models.items():
                if isinstance(model, float):
                    # Degenerate target stored as an empirical prior.
                    probs[th] = model
                else:
                    # predict_proba returns [prob_class_0, prob_class_1]
                    probs[th] = float(model.predict_proba(current_features)[0][1])

            # Enforce logical monotonicity: a >=5% move is a subset of a >=3% move, etc.
            # Therefore P(>=1%) >= P(>=2%) >= P(>=3%) >= P(>=5%). Clamp with a running minimum
            # over ascending thresholds so the displayed/ranked probabilities are coherent.
            running_min = 1.0
            for th in sorted(probs.keys()):
                running_min = min(running_min, probs[th])
                probs[th] = running_min
                
            # Persist prediction (upsert so re-runs refresh stale/incorrect values for the same date)
            existing = self.db.query(Prediction).filter(
                Prediction.stock_id == stock.id,
                Prediction.timestamp == current_date
            ).first()
            
            if existing:
                existing.prob_plus_1 = probs[0.01]
                existing.prob_plus_2 = probs[0.02]
                existing.prob_plus_3 = probs[0.03]
                existing.prob_plus_5 = probs[0.05]
                existing.model_version = "xgb_v3"
                self.db.add(existing)
            else:
                new_pred = Prediction(
                    stock_id=stock.id,
                    timestamp=current_date,
                    prob_plus_1=probs[0.01],
                    prob_plus_2=probs[0.02],
                    prob_plus_3=probs[0.03],
                    prob_plus_5=probs[0.05],
                    model_version="xgb_v3"
                )
                self.db.add(new_pred)
            self.db.commit()
                
            return {
                "prob_plus_1": probs[0.01],
                "prob_plus_2": probs[0.02],
                "prob_plus_3": probs[0.03],
                "prob_plus_5": probs[0.05]
            }

        except Exception as e:
            logger.error(f"Error predicting for {symbol}: {e}")
            return self._get_fallback_probabilities(symbol)

    def _get_fallback_probabilities(self, symbol: str) -> Dict[str, float]:
        """
        Fallback probabilities based on standard stock target priors.
        """
        return {
            "prob_plus_1": 0.1824,
            "prob_plus_2": 0.0891,
            "prob_plus_3": 0.0435,
            "prob_plus_5": 0.0152
        }

    def predict_all_stocks(self):
        active_stocks = self.stock_repo.get_active_stocks()
        for stock in active_stocks:
            try:
                self.predict_next_day(stock.symbol)
            except Exception as e:
                logger.error(f"Prediction run failed for {stock.symbol}: {e}")
