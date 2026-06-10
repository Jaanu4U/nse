"""
Advanced quantitative analytics: risk-adjusted performance metrics, ATR-based
trade levels, trend strength, mean-reversion signals, and walk-forward backtesting
of the next-day prediction model.

These build on data already collected (daily OHLCV + indicators + predictions),
so no additional data source is required.
"""
import logging
import datetime
import os
import pickle
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.repositories.stock_repo import StockRepository
from app.repositories.price_repo import PriceRepository
from app.repositories.indicator_repo import IndicatorRepository
from app.models.models import Prediction

logger = logging.getLogger(__name__)

TRADING_DAYS = 252
# Indian 1-year risk-free proxy (approx). Used for Sharpe/Sortino excess return.
RISK_FREE_ANNUAL = 0.065


class AnalyticsEngine:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)
        self.indicator_repo = IndicatorRepository(db)

    # ------------------------------------------------------------------ helpers
    def _load_prices(self, symbol: str, years: int = 2) -> pd.DataFrame:
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return pd.DataFrame()
        start = datetime.date.today() - datetime.timedelta(days=365 * years)
        prices = self.price_repo.get_daily_prices(stock.id, start, datetime.date.today())
        if len(prices) < 30:
            return pd.DataFrame()
        return pd.DataFrame([{
            "date": p.timestamp,
            "open": float(p.open),
            "high": float(p.high),
            "low": float(p.low),
            "close": float(p.close),
            "volume": int(p.volume),
        } for p in prices]).set_index("date").sort_index()

    def _benchmark_returns(self, index: pd.DatetimeIndex, years: int = 2) -> pd.Series:
        """Daily returns of the NIFTY 50 benchmark, reindexed onto the stock's dates."""
        idx_stock = self.stock_repo.get_by_symbol("NIFTY50IDX")
        if not idx_stock:
            return pd.Series(dtype=float)
        start = datetime.date.today() - datetime.timedelta(days=365 * years)
        idx_prices = self.price_repo.get_daily_prices(idx_stock.id, start, datetime.date.today())
        if len(idx_prices) < 30:
            return pd.Series(dtype=float)
        idx_df = pd.DataFrame([
            {"date": p.timestamp, "close": float(p.close)} for p in idx_prices
        ]).set_index("date").sort_index()
        return idx_df["close"].pct_change()

    # ----------------------------------------------------------- risk metrics
    def risk_metrics(self, symbol: str) -> dict:
        """
        Annualized risk/return metrics: CAGR, volatility, Sharpe, Sortino, Calmar,
        max drawdown and the Ulcer Index.
        """
        df = self._load_prices(symbol, years=2)
        if df.empty:
            return {}

        close = df["close"]
        daily_ret = close.pct_change().dropna()
        if daily_ret.empty:
            return {}

        ann_return = (1 + daily_ret.mean()) ** TRADING_DAYS - 1
        ann_vol = daily_ret.std() * np.sqrt(TRADING_DAYS)
        rf_daily = RISK_FREE_ANNUAL / TRADING_DAYS

        excess = daily_ret - rf_daily
        sharpe = (excess.mean() / daily_ret.std() * np.sqrt(TRADING_DAYS)) if daily_ret.std() > 0 else 0.0

        downside = daily_ret[daily_ret < 0]
        downside_std = downside.std()
        sortino = (excess.mean() / downside_std * np.sqrt(TRADING_DAYS)) if downside_std and downside_std > 0 else 0.0

        # Drawdown curve
        equity = (1 + daily_ret).cumprod()
        running_max = equity.cummax()
        drawdown = equity / running_max - 1
        max_dd = float(drawdown.min())
        calmar = (ann_return / abs(max_dd)) if max_dd < 0 else 0.0
        ulcer = float(np.sqrt((drawdown ** 2).mean()) * 100)

        return {
            "cagr": round(float(ann_return) * 100, 2),
            "volatility": round(float(ann_vol) * 100, 2),
            "sharpe": round(float(sharpe), 2),
            "sortino": round(float(sortino), 2),
            "calmar": round(float(calmar), 2),
            "max_drawdown": round(max_dd * 100, 2),
            "ulcer_index": round(ulcer, 2),
        }

    # -------------------------------------------------- advanced risk metrics
    def advanced_risk_metrics(self, symbol: str) -> dict:
        """
        Tail-risk, distribution and benchmark-relative metrics that complement the
        headline risk_metrics(): Value at Risk (historical + parametric), Conditional
        VaR, beta/alpha vs NIFTY, Treynor, Information & Omega ratios, return skew /
        kurtosis, and high/low range volatility estimators (Parkinson, Garman-Klass,
        Yang-Zhang) which use intraday range and are more efficient than close-to-close.
        """
        df = self._load_prices(symbol, years=2)
        if df.empty:
            return {}

        o, h, l, c = df["open"], df["high"], df["low"], df["close"]
        ret = c.pct_change().dropna()
        if len(ret) < 30:
            return {}

        rf_daily = RISK_FREE_ANNUAL / TRADING_DAYS
        ann_return = (1 + ret.mean()) ** TRADING_DAYS - 1

        # --- Value at Risk (1-day) -------------------------------------------
        # Historical: empirical 5th/1st percentile of the daily return distribution.
        var_95_hist = float(np.percentile(ret, 5))
        var_99_hist = float(np.percentile(ret, 1))
        # Parametric (Gaussian): mean - z * sigma.
        mu, sigma = float(ret.mean()), float(ret.std())
        var_95_param = mu - 1.645 * sigma
        var_99_param = mu - 2.326 * sigma
        # Conditional VaR / Expected Shortfall: average loss beyond the 95% VaR.
        tail = ret[ret <= var_95_hist]
        cvar_95 = float(tail.mean()) if len(tail) else var_95_hist

        # --- Distribution shape ----------------------------------------------
        skew = float(ret.skew())
        kurt = float(ret.kurtosis())  # excess kurtosis (normal = 0)
        # Omega ratio at a 0% daily threshold: gains over losses.
        gains = ret[ret > 0].sum()
        losses = -ret[ret < 0].sum()
        omega = float(gains / losses) if losses > 0 else 0.0
        # Gain-to-Pain: sum of returns over the absolute sum of losses.
        gain_to_pain = float(ret.sum() / losses) if losses > 0 else 0.0
        # Tail ratio: size of the right tail vs the left tail.
        left = abs(np.percentile(ret, 5))
        right = abs(np.percentile(ret, 95))
        tail_ratio = float(right / left) if left > 0 else 0.0

        # --- Benchmark-relative (beta / alpha / Treynor / Information) --------
        bench = self._benchmark_returns(df.index, years=2)
        beta = alpha = treynor = info_ratio = None
        if not bench.empty:
            joined = pd.concat([ret, bench], axis=1, join="inner").dropna()
            joined.columns = ["stock", "bench"]
            if len(joined) >= 30:
                var_b = joined["bench"].var()
                cov = joined["stock"].cov(joined["bench"])
                beta = float(cov / var_b) if var_b > 0 else None
                ann_bench = (1 + joined["bench"].mean()) ** TRADING_DAYS - 1
                if beta is not None:
                    # Jensen's alpha (annualized), CAPM expected return.
                    alpha = float((ann_return - (RISK_FREE_ANNUAL + beta * (ann_bench - RISK_FREE_ANNUAL))) * 100)
                    treynor = float((ann_return - RISK_FREE_ANNUAL) / beta) if beta != 0 else None
                # Information ratio: active return / tracking error (annualized).
                active = joined["stock"] - joined["bench"]
                te = active.std()
                info_ratio = float(active.mean() / te * np.sqrt(TRADING_DAYS)) if te > 0 else None

        # --- Range-based volatility estimators (annualized %) ----------------
        ln_hl = np.log(h / l)
        parkinson = float(np.sqrt((ln_hl ** 2).mean() / (4 * np.log(2))) * np.sqrt(TRADING_DAYS) * 100)
        ln_co = np.log(c / o)
        gk = float(np.sqrt((0.5 * ln_hl ** 2 - (2 * np.log(2) - 1) * ln_co ** 2).mean())
                   * np.sqrt(TRADING_DAYS) * 100)
        # Yang-Zhang: combines overnight, open-close and Rogers-Satchell variances.
        overnight = np.log(o / c.shift(1)).dropna()
        open_close = np.log(c / o)
        rs = (np.log(h / c) * np.log(h / o) + np.log(l / c) * np.log(l / o))
        n = len(overnight)
        yang_zhang = None
        if n > 2:
            k = 0.34 / (1.34 + (n + 1) / (n - 1))
            var_o = overnight.var()
            var_c = open_close.loc[overnight.index].var()
            var_rs = rs.loc[overnight.index].mean()
            yz = np.sqrt(var_o + k * var_c + (1 - k) * var_rs) * np.sqrt(TRADING_DAYS) * 100
            yang_zhang = float(yz) if np.isfinite(yz) else None

        return {
            "var_95_hist": round(var_95_hist * 100, 2),
            "var_99_hist": round(var_99_hist * 100, 2),
            "var_95_param": round(var_95_param * 100, 2),
            "var_99_param": round(var_99_param * 100, 2),
            "cvar_95": round(cvar_95 * 100, 2),
            "skew": round(skew, 2),
            "kurtosis": round(kurt, 2),
            "omega": round(omega, 2),
            "gain_to_pain": round(gain_to_pain, 2),
            "tail_ratio": round(tail_ratio, 2),
            "beta": round(beta, 2) if beta is not None else None,
            "alpha": round(alpha, 2) if alpha is not None else None,
            "treynor": round(treynor, 2) if treynor is not None else None,
            "information_ratio": round(info_ratio, 2) if info_ratio is not None else None,
            "vol_parkinson": round(parkinson, 2),
            "vol_garman_klass": round(gk, 2),
            "vol_yang_zhang": round(yang_zhang, 2) if yang_zhang is not None else None,
        }


    # ------------------------------------------------------- trend / reversion
    def trend_strength(self, symbol: str, window: int = 60) -> dict:
        """
        Linear-regression slope (annualized %) and R^2 over a window, plus
        mean-reversion context: Bollinger %B and a 20-day z-score.
        """
        df = self._load_prices(symbol, years=1)
        if df.empty or len(df) < window:
            return {}

        close = df["close"]
        recent = close.iloc[-window:]
        x = np.arange(len(recent))
        log_p = np.log(recent.values)
        slope, intercept = np.polyfit(x, log_p, 1)
        fit = slope * x + intercept
        ss_res = np.sum((log_p - fit) ** 2)
        ss_tot = np.sum((log_p - log_p.mean()) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        ann_slope = (np.exp(slope * TRADING_DAYS) - 1) * 100

        # Bollinger %B (20, 2 sigma)
        ma20 = close.rolling(20).mean()
        sd20 = close.rolling(20).std()
        upper = ma20 + 2 * sd20
        lower = ma20 - 2 * sd20
        last_close = close.iloc[-1]
        rng = (upper.iloc[-1] - lower.iloc[-1])
        percent_b = float((last_close - lower.iloc[-1]) / rng) if rng > 0 else 0.5

        # 20-day z-score (mean reversion magnitude)
        z = float((last_close - ma20.iloc[-1]) / sd20.iloc[-1]) if sd20.iloc[-1] and sd20.iloc[-1] > 0 else 0.0

        if ann_slope > 5 and r2 > 0.5:
            regime = "STRONG_UPTREND"
        elif ann_slope > 0:
            regime = "UPTREND"
        elif ann_slope < -5 and r2 > 0.5:
            regime = "STRONG_DOWNTREND"
        else:
            regime = "DOWNTREND" if ann_slope < 0 else "SIDEWAYS"

        return {
            "trend_slope_annual": round(float(ann_slope), 2),
            "trend_r2": round(float(r2), 3),
            "regime": regime,
            "percent_b": round(percent_b, 3),
            "zscore_20": round(z, 2),
        }

    # --------------------------------------------------------- trade levels
    def trade_levels(self, symbol: str, atr_mult_sl: float = 1.5, atr_mult_target: float = 3.0) -> dict:
        """
        ATR-based actionable trade plan: entry (last close), stop-loss, target and
        the resulting risk-reward ratio. Also classic pivot support/resistance.
        """
        df = self._load_prices(symbol, years=1)
        if df.empty:
            return {}

        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]

        entry = float(close.iloc[-1])
        if not np.isfinite(atr) or atr <= 0:
            return {"entry": round(entry, 2)}

        stop_loss = entry - atr_mult_sl * atr
        target = entry + atr_mult_target * atr
        risk = entry - stop_loss
        reward = target - entry
        rr = reward / risk if risk > 0 else 0.0

        # Classic pivot points from the last bar
        h, l, c = float(high.iloc[-1]), float(low.iloc[-1]), float(close.iloc[-1])
        pivot = (h + l + c) / 3
        r1 = 2 * pivot - l
        s1 = 2 * pivot - h
        r2 = pivot + (h - l)
        s2 = pivot - (h - l)

        return {
            "entry": round(entry, 2),
            "stop_loss": round(float(stop_loss), 2),
            "target": round(float(target), 2),
            "atr": round(float(atr), 2),
            "risk_reward": round(float(rr), 2),
            "pivot": round(pivot, 2),
            "resistance_1": round(r1, 2),
            "support_1": round(s1, 2),
            "resistance_2": round(r2, 2),
            "support_2": round(s2, 2),
        }

    # ------------------------------------------------------------- backtest
    def backtest_prediction(self, symbol: str, threshold: float = 0.01, prob_cut: float = 0.5) -> dict:
        """
        Walk-forward style evaluation of the cached +threshold model on the most
        recent slice of history. Measures how often a 'buy' signal (prob >= prob_cut)
        was followed by the stock actually moving >= threshold the next day.

        Note: uses the persisted model on a held-out tail of the feature matrix.
        It is an in-sample-aware sanity check, not a leak-free production backtest,
        but it surfaces obvious model failure (accuracy no better than the base rate).
        """
        from app.services.prediction import PredictionEngine, MODEL_DIR

        engine = PredictionEngine(self.db)
        X, y = engine._prepare_data(symbol)
        if X is None or len(X) < 150:
            return {"error": "insufficient_data"}

        model_path = os.path.join(MODEL_DIR, f"{symbol}_models.pkl")
        if not os.path.exists(model_path):
            if not engine.train_models(symbol):
                return {"error": "no_model"}
        with open(model_path, "rb") as f:
            models = pickle.load(f)

        model = models.get(threshold)
        # Evaluate on the most recent 20% of rows that have a known next-day target.
        X_eval = X.iloc[:-1]
        y_raw = y.iloc[:-1]["next_day_return"]
        n = len(X_eval)
        split = int(n * 0.8)
        Xe, ye = X_eval.iloc[split:], y_raw.iloc[split:]
        if len(Xe) < 20:
            return {"error": "insufficient_data"}

        actual = (ye >= threshold).astype(int)
        base_rate = float(actual.mean())

        if isinstance(model, float):
            probs = np.full(len(Xe), model)
        elif model is None:
            return {"error": "no_threshold_model"}
        else:
            probs = model.predict_proba(Xe)[:, 1]

        signals = (probs >= prob_cut).astype(int)
        n_signals = int(signals.sum())

        # Hit-rate among the bars where the model fired a buy signal.
        if n_signals > 0:
            hit_rate = float(((signals == 1) & (actual == 1)).sum() / n_signals)
        else:
            hit_rate = 0.0

        # Overall directional accuracy of the binary call.
        accuracy = float((signals == actual.values).mean())

        # Simple strategy: go long next day when signal fires; average realized return.
        realized = ye.values[signals == 1]
        avg_signal_return = float(np.mean(realized)) if realized.size else 0.0

        return {
            "threshold": threshold,
            "samples_evaluated": int(len(Xe)),
            "base_rate": round(base_rate * 100, 1),
            "signals_fired": n_signals,
            "signal_hit_rate": round(hit_rate * 100, 1),
            "edge_vs_base": round((hit_rate - base_rate) * 100, 1),
            "directional_accuracy": round(accuracy * 100, 1),
            "avg_return_on_signal": round(avg_signal_return * 100, 2),
        }

    # -------------------------------------------------------------- bundle
    def full_analytics(self, symbol: str) -> dict:
        return {
            "symbol": symbol.upper(),
            "risk_metrics": self.risk_metrics(symbol),
            "advanced_risk": self.advanced_risk_metrics(symbol),
            "trend": self.trend_strength(symbol),
            "trade_levels": self.trade_levels(symbol),
        }
