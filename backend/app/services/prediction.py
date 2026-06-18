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
import lightgbm as lgb
import datetime
from typing import Dict, Any, Tuple, Optional
from app.config import settings

logger = logging.getLogger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "models")
os.makedirs(MODEL_DIR, exist_ok=True)

# Ascending move-size targets the classifiers predict probabilities for.
# These are next-day CLOSE-TO-CLOSE thresholds, i.e.
# P(prob_plus_2) = P((next_close - close)/close >= 2%). A 30-day realized buy-open/
# sell-close OOS (backtest_oc.py) found this target is the only one with positive edge
# over the liquid-universe baseline on BOTH NSE and USA; an open->high target was strong
# on NSE but had ~zero edge on USA, and a direct open->close target was weakest everywhere.
THRESHOLDS = [0.01, 0.02, 0.03, 0.05]
# Lookback window (sessions) used to build sequences for the optional LSTM backend.
LSTM_WINDOW = 30

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

        # Bollinger %B: where close sits inside the 20-SMA ± 2σ band (0 = lower, 1 = upper).
        sma_20 = df['close'].rolling(window=20).mean()
        std_20 = df['close'].rolling(window=20).std()
        bb_upper = sma_20 + 2 * std_20
        bb_lower = sma_20 - 2 * std_20
        df['bb_pct'] = (df['close'] - bb_lower) / (bb_upper - bb_lower)

        # Overnight gap: open relative to the prior close.
        df['gap_open'] = (df['open'] - df['close'].shift(1)) / df['close'].shift(1)

        # Volume z-score over 20 sessions (spike detection, scale-free).
        vol_std_20 = df['volume'].rolling(window=20).std()
        df['vol_zscore_20'] = (df['volume'] - df['vol_sma_20']) / vol_std_20

        # Intraday range as a fraction of close (per-bar volatility proxy).
        df['range_pct'] = (df['high'] - df['low']) / df['close']

        # --- Open->High targeting features: describe whether tomorrow is likely to
        # print a large open->high move. All use data up to and including day t only.
        rng_mean_20 = df['range_pct'].rolling(window=20, min_periods=5).mean()
        df['atr_expansion'] = df['range_pct'] / rng_mean_20.replace(0, np.nan)
        hl_range = (df['high'] - df['low'])
        df['close_pos_range'] = ((df['close'] - df['low']) / hl_range.replace(0, np.nan)).clip(0, 1)
        oh_today = (df['high'] - df['open']) / df['open']
        df['oh_today'] = oh_today
        df['oh_mean_20'] = oh_today.rolling(window=20, min_periods=5).mean()
        df['oh_freq2_20'] = (oh_today >= 0.02).rolling(window=20, min_periods=5).mean()
        prior_high_5 = df['high'].shift(1).rolling(window=5, min_periods=2).max()
        df['dist_prior_high5'] = (df['close'] - prior_high_5) / prior_high_5

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

        # Next-day return (kept for reference / baseline backtest scripts).
        df['next_close'] = df['close'].shift(-1)
        df['next_day_return'] = (df['next_close'] - df['close']) / df['close']

        # Next-day open->high (the production training target: the exact graded metric).
        df['next_open'] = df['open'].shift(-1)
        df['next_high'] = df['high'].shift(-1)
        df['next_oh'] = (df['next_high'] - df['next_open']) / df['next_open']

        # Clean NaNs
        df = df.dropna(subset=['rsi', 'macd_hist', 'volume_ratio', 'dist_ema_20', 'atr_ratio', 'adx'])
        # Fill the newer feature columns (early-history NaNs) with neutral values so rows are retained.
        fill_defaults = {
            'stoch_k': 50.0, 'mfi': 50.0, 'cci': 0.0, 'williams_r': -50.0, 'supertrend_dir': 0.0,
            'roc_10': 0.0, 'roc_20': 0.0, 'realized_vol_20': 0.0,
            'dist_52w_high': 0.0, 'dist_52w_low': 0.0, 'rs_nifty_20': 0.0, 'beta_60': 1.0,
            'bb_pct': 0.5, 'gap_open': 0.0, 'vol_zscore_20': 0.0, 'range_pct': 0.0,
            'atr_expansion': 1.0, 'close_pos_range': 0.5, 'oh_today': 0.0,
            'oh_mean_20': 0.0, 'oh_freq2_20': 0.0, 'dist_prior_high5': 0.0,
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
            'bb_pct', 'gap_open', 'vol_zscore_20', 'range_pct',
            'atr_expansion', 'close_pos_range', 'oh_today',
            'oh_mean_20', 'oh_freq2_20', 'dist_prior_high5',
        ]
        
        X = df[feature_cols].copy()
        y = df[['next_day_return', 'next_oh']].copy()
        
        return X, y

    # ------------------------------------------------------------------
    # Model backend helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_model_type(model_type: Optional[str]) -> str:
        mt = (model_type or settings.PREDICTION_MODEL or "ensemble").lower()
        if mt not in ("ensemble", "xgb", "lgbm", "lstm"):
            mt = "ensemble"
        return mt

    @staticmethod
    def _model_path(symbol: str, model_type: str) -> str:
        tag = {"ensemble": "ens", "lgbm": "lgbm", "xgb": "xgb"}.get(model_type, "xgb")
        return os.path.join(MODEL_DIR, f"{symbol}_{tag}_models.pkl")

    @staticmethod
    def _new_estimator(kind: str):
        """Create a fresh, regularized gradient-boosting classifier."""
        if kind == "lgbm":
            # LightGBM: leaf-wise growth, capped to curb overfitting on rare big-move targets.
            return lgb.LGBMClassifier(
                n_estimators=300,
                max_depth=4,
                num_leaves=15,
                learning_rate=0.03,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_samples=20,
                reg_lambda=2.0,
                random_state=42,
                verbose=-1,
            )
        # XGBoost: subsampling, min_child_weight and L2 regularization curb the severe
        # overfitting that otherwise inflates probabilities for big-move (>=3%, >=5%) targets.
        return xgb.XGBClassifier(
            n_estimators=120,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=5,
            reg_lambda=2.0,
            random_state=42,
            eval_metric="logloss",
        )

    def train_models(self, symbol: str, model_type: Optional[str] = None) -> bool:
        """
        Train binary classifiers for each target threshold (+1%, +2%, +3%, +5%).

        The backend is selected by ``model_type`` (or ``settings.PREDICTION_MODEL``):
        ``ensemble`` (XGBoost + LightGBM), ``xgb``, ``lgbm`` or ``lstm``.
        """
        model_type = self._resolve_model_type(model_type)
        if model_type == "lstm":
            return self._train_lstm(symbol)

        X, y = self._prepare_data(symbol)
        if X is None or len(X) < 100:
            logger.warning(f"Insufficient historical data to train model for {symbol}.")
            return False

        # Drop the last row of X since it doesn't have a next-day target (shifted)
        X_train = X.iloc[:-1]
        # Train on the close-to-close target (most robust realized PnL across markets;
        # see backtest_oc.py 30-day OOS).
        y_train_raw = y.iloc[:-1]['next_day_return']

        models: Dict[float, Any] = {}
        for th in THRESHOLDS:
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

            if model_type == "ensemble":
                # Train both boosters; predictions average their probabilities, which
                # reduces single-model variance and tends to calibrate better.
                kinds = ["xgb", "lgbm"]
                ests = []
                for kind in kinds:
                    est = self._new_estimator(kind)
                    est.fit(X_train, y_bin)
                    ests.append(est)
                models[th] = ests
            else:
                est = self._new_estimator(model_type)
                est.fit(X_train, y_bin)
                models[th] = est

        wrapper = {"model_type": model_type, "features": list(X.columns), "models": models}
        with open(self._model_path(symbol, model_type), "wb") as f:
            pickle.dump(wrapper, f)

        logger.info(f"Trained next-day prediction models for {symbol} ({model_type}).")
        return True

    @staticmethod
    def _proba_from(model, features) -> float:
        """Probability of the positive class from a single estimator, ensemble list, or prior."""
        if isinstance(model, float):
            return model
        if isinstance(model, list):
            vals = [float(m.predict_proba(features)[0][1]) for m in model]
            return float(np.mean(vals)) if vals else 0.0
        return float(model.predict_proba(features)[0][1])

    def predict_next_day(self, symbol: str, model_type: Optional[str] = None) -> Dict[str, float]:
        """
        Predict probability scores of stock moving +1%, +2%, +3%, +5% tomorrow.
        """
        model_type = self._resolve_model_type(model_type)
        if model_type == "lstm":
            return self._predict_lstm(symbol)
        return self._predict_boosting(symbol, model_type)

    def _predict_boosting(self, symbol: str, model_type: str) -> Dict[str, float]:
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

        # Try loading models; if not trained, train now.
        model_path = self._model_path(symbol, model_type)
        if not os.path.exists(model_path):
            success = self.train_models(symbol, model_type)
            if not success:
                return self._get_fallback_probabilities(symbol)

        try:
            with open(model_path, "rb") as f:
                wrapper = pickle.load(f)
            models = wrapper["models"] if isinstance(wrapper, dict) and "models" in wrapper else wrapper

            probs = {th: self._proba_from(model, current_features) for th, model in models.items()}
            base_rates = self._historical_base_rates(y)
            probs = self._monotonic_with_base_rates(probs, base_rates)
            self._persist_prediction(stock, current_date, probs, f"{model_type}_v4")
            return self._as_response(probs)

        except Exception as e:
            logger.error(f"Error predicting for {symbol}: {e}")
            return self._get_fallback_probabilities(symbol)

    # ------------------------------------------------------------------
    # Optional LSTM backend (sequence-aware). Requires TensorFlow; if it is
    # not installed the engine transparently falls back to the boosting ensemble.
    # ------------------------------------------------------------------
    @staticmethod
    def _lstm_paths(symbol: str) -> Tuple[str, str]:
        meta_path = os.path.join(MODEL_DIR, f"{symbol}_lstm_meta.pkl")
        return meta_path, MODEL_DIR

    @staticmethod
    def _build_sequences(arr: np.ndarray, window: int) -> np.ndarray:
        """Build sliding [n, window, features] sequences ending at each row (>= window)."""
        seqs = [arr[i - window:i] for i in range(window, len(arr) + 1)]
        return np.asarray(seqs, dtype=np.float32)

    def _train_lstm(self, symbol: str) -> bool:
        try:
            import tensorflow as tf  # noqa: F401
            from sklearn.preprocessing import StandardScaler
        except Exception as e:
            logger.warning(f"TensorFlow unavailable ({e}); LSTM training skipped for {symbol}.")
            return False

        X, y = self._prepare_data(symbol)
        if X is None or len(X) < (LSTM_WINDOW + 100):
            logger.warning(f"Insufficient data for LSTM on {symbol}.")
            return False

        X_train = X.iloc[:-1]
        y_train_raw = y.iloc[:-1]['next_day_return']

        scaler = StandardScaler()
        scaled = scaler.fit_transform(X_train.values)
        seqs = self._build_sequences(scaled, LSTM_WINDOW)  # [n_seq, window, n_feat]
        # Targets align to the last row of each sequence.
        aligned_returns = y_train_raw.values[LSTM_WINDOW - 1:]

        priors: Dict[float, float] = {}
        n_feat = X_train.shape[1]
        for th in THRESHOLDS:
            y_bin = (aligned_returns >= th).astype(int)
            n_pos = int(y_bin.sum())
            if n_pos == 0:
                priors[th] = 0.0
                continue
            if n_pos == len(y_bin):
                priors[th] = 1.0
                continue

            model = tf.keras.Sequential([
                tf.keras.layers.Input(shape=(LSTM_WINDOW, n_feat)),
                tf.keras.layers.LSTM(64, return_sequences=True),
                tf.keras.layers.Dropout(0.2),
                tf.keras.layers.LSTM(32),
                tf.keras.layers.Dense(1, activation="sigmoid"),
            ])
            model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
            # Weight the rare positive class so big-move targets aren't ignored.
            neg, pos = len(y_bin) - n_pos, n_pos
            class_weight = {0: 1.0, 1: max(1.0, neg / max(pos, 1))}
            model.fit(seqs, y_bin, epochs=15, batch_size=32, verbose=0, class_weight=class_weight)
            model.save(os.path.join(MODEL_DIR, f"{symbol}_lstm_th{int(th * 100)}.keras"))

        meta_path, _ = self._lstm_paths(symbol)
        with open(meta_path, "wb") as f:
            pickle.dump({"scaler": scaler, "features": list(X.columns), "priors": priors}, f)
        logger.info(f"Trained LSTM next-day models for {symbol}.")
        return True

    def _predict_lstm(self, symbol: str) -> Dict[str, float]:
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return {}

        meta_path, _ = self._lstm_paths(symbol)
        if not os.path.exists(meta_path):
            if not self._train_lstm(symbol):
                # TensorFlow missing or not enough data -> fall back to the boosting ensemble.
                return self._predict_boosting(symbol, "ensemble")

        try:
            import tensorflow as tf
        except Exception as e:
            logger.warning(f"TensorFlow unavailable ({e}); LSTM prediction falling back for {symbol}.")
            return self._predict_boosting(symbol, "ensemble")

        X, y = self._prepare_data(symbol)
        if X is None or len(X) < LSTM_WINDOW:
            return self._get_fallback_probabilities(symbol)

        try:
            with open(meta_path, "rb") as f:
                meta = pickle.load(f)
            scaler = meta["scaler"]
            priors = meta.get("priors", {})
            current_date = X.index[-1]

            window_scaled = scaler.transform(X.iloc[-LSTM_WINDOW:].values)
            seq = window_scaled.reshape(1, LSTM_WINDOW, X.shape[1]).astype(np.float32)

            probs: Dict[float, float] = {}
            for th in THRESHOLDS:
                if th in priors:
                    probs[th] = float(priors[th])
                    continue
                model_file = os.path.join(MODEL_DIR, f"{symbol}_lstm_th{int(th * 100)}.keras")
                if not os.path.exists(model_file):
                    probs[th] = 0.0
                    continue
                model = tf.keras.models.load_model(model_file)
                probs[th] = float(model.predict(seq, verbose=0)[0][0])

            base_rates = self._historical_base_rates(y)
            probs = self._monotonic_with_base_rates(probs, base_rates)
            self._persist_prediction(stock, current_date, probs, "lstm_v4")
            return self._as_response(probs)

        except Exception as e:
            logger.error(f"Error in LSTM prediction for {symbol}: {e}")
            return self._predict_boosting(symbol, "ensemble")

    # ------------------------------------------------------------------
    # Shared prediction utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _historical_base_rates(y: Optional[pd.DataFrame]) -> Dict[float, float]:
        """
        Empirical share of past sessions whose next-day close-to-close return cleared each
        threshold. Used to anchor the rarer-move tail so probabilities decay with rarity.
        Excludes the final row (its forward target is undefined).
        """
        rates: Dict[float, float] = {}
        if y is None or 'next_day_return' not in y.columns:
            return rates
        rets = y['next_day_return'].iloc[:-1].dropna()
        if len(rets) == 0:
            return rates
        for th in THRESHOLDS:
            rates[th] = float((rets >= th).mean())
        return rates

    @staticmethod
    def _clamp_monotonic(probs: Dict[float, float]) -> Dict[float, float]:
        """
        Enforce logical monotonicity: a >=5% move is a subset of a >=3% move, etc.
        Therefore P(>=1%) >= P(>=2%) >= P(>=3%) >= P(>=5%). Clamp with a running minimum
        over ascending thresholds so the ranked probabilities are coherent.
        """
        running_min = 1.0
        out = {}
        for th in sorted(probs.keys()):
            running_min = min(running_min, probs[th])
            out[th] = running_min
        return out

    @staticmethod
    def _monotonic_with_base_rates(
        probs: Dict[float, float], base_rates: Dict[float, float]
    ) -> Dict[float, float]:
        """
        Enforce a genuinely *decaying* tail using historical base rates.

        The per-threshold classifiers are trained independently, so a rarer (higher)
        threshold can emit a HIGHER raw probability than a nearer one
        (e.g. raw P(>=5%) > raw P(>=3%)), which is impossible because a >=5% move is a
        strict subset of a >=3% move. A plain running-min clamp only *ties* them
        (P(>=3%) == P(>=5%)), masking the miscalibrated tail model.

        Instead, cap each higher threshold at the previous (more reliable) one scaled by
        the stock's historical conditional rate ``base[t_i] / base[t_{i-1}]`` and take the
        smaller of that cap and the model's raw value. This lets the tail model push a
        probability *down* (genuine bearish signal) but never *up* past the rarity implied
        by history, so the ladder strictly decays. Falls back to a plain min-clamp when a
        base rate is unavailable.
        """
        out: Dict[float, float] = {}
        prev_th: Optional[float] = None
        for th in sorted(probs.keys()):
            p = float(probs[th])
            if prev_th is None:
                out[th] = p
                prev_th = th
                continue
            b_prev = float(base_rates.get(prev_th, 0.0))
            b_cur = float(base_rates.get(th, 0.0))
            if b_prev > 0.0:
                ratio = min(max(b_cur / b_prev, 0.0), 1.0)
                cap = out[prev_th] * ratio
            else:
                # No history for the rarer move: it cannot exceed the nearer one.
                cap = out[prev_th]
            out[th] = min(p, cap)
            prev_th = th
        return out

    def _persist_prediction(self, stock, current_date, probs: Dict[float, float], model_version: str):
        # Upsert so re-runs refresh stale/incorrect values for the same date.
        existing = self.db.query(Prediction).filter(
            Prediction.stock_id == stock.id,
            Prediction.timestamp == current_date
        ).first()

        if existing:
            existing.prob_plus_1 = probs[0.01]
            existing.prob_plus_2 = probs[0.02]
            existing.prob_plus_3 = probs[0.03]
            existing.prob_plus_5 = probs[0.05]
            existing.model_version = model_version
            self.db.add(existing)
        else:
            self.db.add(Prediction(
                stock_id=stock.id,
                timestamp=current_date,
                prob_plus_1=probs[0.01],
                prob_plus_2=probs[0.02],
                prob_plus_3=probs[0.03],
                prob_plus_5=probs[0.05],
                model_version=model_version,
            ))
        self.db.commit()

    @staticmethod
    def _as_response(probs: Dict[float, float]) -> Dict[str, float]:
        return {
            "prob_plus_1": probs[0.01],
            "prob_plus_2": probs[0.02],
            "prob_plus_3": probs[0.03],
            "prob_plus_5": probs[0.05],
        }

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
        # The full universe runs on the boosting backend only; LSTM is far too slow to
        # train per-symbol across thousands of stocks, so it is downgraded to the ensemble.
        model_type = self._resolve_model_type(None)
        if model_type == "lstm":
            model_type = "ensemble"
        active_stocks = self.stock_repo.get_active_stocks()
        for stock in active_stocks:
            try:
                self.predict_next_day(stock.symbol, model_type=model_type)
            except Exception as e:
                logger.error(f"Prediction run failed for {stock.symbol}: {e}")
