import pandas as pd
import numpy as np
import logging
from sqlalchemy.orm import Session
from app.repositories.price_repo import PriceRepository
from app.repositories.indicator_repo import IndicatorRepository
from app.repositories.stock_repo import StockRepository
import ta
import datetime

logger = logging.getLogger(__name__)

class TechnicalAnalysisEngine:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)
        self.indicator_repo = IndicatorRepository(db)

    def calculate_and_save_indicators(self, symbol: str, start_date: datetime.date = None, end_date: datetime.date = None) -> int:
        """
        Calculate and persist all daily technical indicators for a given symbol.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            logger.error(f"Stock {symbol} not found in database.")
            return 0

        # Default query range: last 2 years to today (to ensure enough data for EMA200)
        if not start_date:
            start_date = datetime.date.today() - datetime.timedelta(days=365 * 2)
        if not end_date:
            end_date = datetime.date.today()

        # Fetch daily prices
        prices = self.price_repo.get_daily_prices(stock.id, start_date, end_date)
        # ADXIndicator(window=14) builds an internal array of size n-(window-1);
        # it then accesses [window] = [14] so needs n - 13 > 14 → n >= 28.
        if len(prices) < 28:
            logger.warning(f"Not enough prices to calculate indicators for {symbol} (only {len(prices)} candles).")
            return 0

        # Load into DataFrame
        df = pd.DataFrame([{
            'date': p.timestamp,
            'open': float(p.open),
            'high': float(p.high),
            'low': float(p.low),
            'close': float(p.close),
            'volume': int(p.volume)
        } for p in prices])

        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)

        # 1. RSI (14)
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()

        # 2. MACD
        macd_indicator = ta.trend.MACD(close=df['close'], window_fast=12, window_slow=26, window_sign=9)
        df['macd'] = macd_indicator.macd()
        df['macd_signal'] = macd_indicator.macd_signal()
        df['macd_hist'] = macd_indicator.macd_diff()

        # 3. Moving Averages
        df['ema_20'] = ta.trend.ema_indicator(close=df['close'], window=20)
        df['ema_50'] = ta.trend.ema_indicator(close=df['close'], window=50)
        df['ema_200'] = ta.trend.ema_indicator(close=df['close'], window=200)
        df['sma_20'] = ta.trend.sma_indicator(close=df['close'], window=20)

        # 4. Bollinger Bands
        bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_middle'] = bb.bollinger_mavg()
        df['bb_lower'] = bb.bollinger_lband()

        # 5. VWAP (Volume Weighted Average Price) - rolling 20-day anchored window.
        # A cumulative VWAP over a 2-year dataset is dominated by stale data and barely moves,
        # so a 20-day rolling VWAP is used to track recent volume-weighted fair value.
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        tp_vol = (typical_price * df['volume']).rolling(window=20).sum()
        vol_sum = df['volume'].rolling(window=20).sum()
        df['vwap'] = tp_vol / vol_sum.replace(0, np.nan)

        # 6. ATR (Average True Range)
        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()

        # 7. ADX (Average Directional Index)
        df['adx'] = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14).adx()

        # 8. Ichimoku Cloud
        # Tenkan-sen (Conversion Line): (9-period high + 9-period low) / 2
        high_9 = df['high'].rolling(window=9).max()
        low_9 = df['low'].rolling(window=9).min()
        df['ichimoku_tenkan'] = (high_9 + low_9) / 2

        # Kijun-sen (Base Line): (26-period high + 26-period low) / 2
        high_26 = df['high'].rolling(window=26).max()
        low_26 = df['low'].rolling(window=26).min()
        df['ichimoku_kijun'] = (high_26 + low_26) / 2

        # Senkou Span A (Leading Span A): (Conversion Line + Base Line) / 2 (shifted forward by 26 periods)
        df['ichimoku_senkou_a'] = ((df['ichimoku_tenkan'] + df['ichimoku_kijun']) / 2).shift(26)

        # Senkou Span B (Leading Span B): (52-period high + 52-period low) / 2 (shifted forward by 26 periods)
        high_52 = df['high'].rolling(window=52).max()
        low_52 = df['low'].rolling(window=52).min()
        df['ichimoku_senkou_b'] = ((high_52 + low_52) / 2).shift(26)

        # 9. Stochastic Oscillator (%K 14, %D 3) - momentum / reversal
        stoch = ta.momentum.StochasticOscillator(
            high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3
        )
        df['stoch_k'] = stoch.stoch()
        df['stoch_d'] = stoch.stoch_signal()

        # 10. Money Flow Index (14) - volume-weighted RSI
        df['mfi'] = ta.volume.MFIIndicator(
            high=df['high'], low=df['low'], close=df['close'], volume=df['volume'], window=14
        ).money_flow_index()

        # 11. CCI (Commodity Channel Index, 20)
        df['cci'] = ta.trend.CCIIndicator(
            high=df['high'], low=df['low'], close=df['close'], window=20
        ).cci()

        # 12. Williams %R (14)
        df['williams_r'] = ta.momentum.WilliamsRIndicator(
            high=df['high'], low=df['low'], close=df['close'], lbp=14
        ).williams_r()

        # 13. On-Balance Volume (cumulative volume flow)
        df['obv'] = ta.volume.OnBalanceVolumeIndicator(
            close=df['close'], volume=df['volume']
        ).on_balance_volume()

        # 14. Supertrend (ATR 10, multiplier 3) - trend-following overlay + direction flag
        supertrend, supertrend_dir = self._supertrend(df, period=10, multiplier=3.0)
        df['supertrend'] = supertrend
        df['supertrend_dir'] = supertrend_dir

        # Replace NaN values with None for database compatibility
        df = df.replace({np.nan: None})

        # Compile data to save
        indicators_to_save = []
        for timestamp, row in df.iterrows():
            indicators_to_save.append({
                "stock_id": stock.id,
                "timestamp": timestamp,
                "rsi": row['rsi'],
                "macd": row['macd'],
                "macd_signal": row['macd_signal'],
                "macd_hist": row['macd_hist'],
                "ema_20": row['ema_20'],
                "ema_50": row['ema_50'],
                "ema_200": row['ema_200'],
                "sma_20": row['sma_20'],
                "bb_upper": row['bb_upper'],
                "bb_middle": row['bb_middle'],
                "bb_lower": row['bb_lower'],
                "vwap": row['vwap'],
                "atr": row['atr'],
                "adx": row['adx'],
                "ichimoku_tenkan": row['ichimoku_tenkan'],
                "ichimoku_kijun": row['ichimoku_kijun'],
                "ichimoku_senkou_a": row['ichimoku_senkou_a'],
                "ichimoku_senkou_b": row['ichimoku_senkou_b'],
                "stoch_k": row['stoch_k'],
                "stoch_d": row['stoch_d'],
                "mfi": row['mfi'],
                "cci": row['cci'],
                "williams_r": row['williams_r'],
                "obv": row['obv'],
                "supertrend": row['supertrend'],
                "supertrend_dir": int(row['supertrend_dir']) if row['supertrend_dir'] is not None else None,
            })

        if indicators_to_save:
            self.indicator_repo.bulk_upsert(indicators_to_save)
            logger.info(f"Calculated and saved {len(indicators_to_save)} indicators for {symbol}.")
            return len(indicators_to_save)
            
        return 0

    @staticmethod
    def _supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
        """
        Compute the Supertrend overlay and its trend direction.
        Returns (supertrend_series, direction_series) where direction is +1 (bullish) / -1 (bearish).
        """
        high, low, close = df['high'], df['low'], df['close']
        atr = ta.volatility.AverageTrueRange(
            high=high, low=low, close=close, window=period
        ).average_true_range()

        hl2 = (high + low) / 2
        upper_basic = hl2 + multiplier * atr
        lower_basic = hl2 - multiplier * atr

        n = len(df)
        upper = np.full(n, np.nan)
        lower = np.full(n, np.nan)
        st = np.full(n, np.nan)
        direction = np.full(n, np.nan)

        close_vals = close.to_numpy()
        ub = upper_basic.to_numpy()
        lb = lower_basic.to_numpy()

        for i in range(n):
            if np.isnan(ub[i]) or np.isnan(lb[i]):
                continue
            if np.isnan(upper[i - 1]) if i > 0 else True:
                # First valid bar: seed bands and an initial bullish direction.
                upper[i] = ub[i]
                lower[i] = lb[i]
                direction[i] = 1
                st[i] = lower[i]
                continue

            upper[i] = ub[i] if (ub[i] < upper[i - 1] or close_vals[i - 1] > upper[i - 1]) else upper[i - 1]
            lower[i] = lb[i] if (lb[i] > lower[i - 1] or close_vals[i - 1] < lower[i - 1]) else lower[i - 1]

            prev_dir = direction[i - 1]
            if prev_dir == 1:
                direction[i] = -1 if close_vals[i] < lower[i] else 1
            else:
                direction[i] = 1 if close_vals[i] > upper[i] else -1

            st[i] = lower[i] if direction[i] == 1 else upper[i]

        return pd.Series(st, index=df.index), pd.Series(direction, index=df.index)

    def calculate_all_active_stocks(self) -> int:
        """
        Calculate daily indicators for all active stocks.
        """
        active_stocks = self.stock_repo.get_active_stocks()
        total_calculated = 0
        for stock in active_stocks:
            try:
                count = self.calculate_and_save_indicators(stock.symbol)
                total_calculated += count
            except Exception as e:
                logger.error(f"Failed to calculate indicators for {stock.symbol}: {e}")
        return total_calculated

    # ----------------------------------------------------- extra indicators
    def compute_extra_indicators(self, symbol: str) -> dict:
        """
        Compute an additional family of indicators on the fly (not persisted):
        Parabolic SAR, Donchian & Keltner channels, Chaikin Money Flow, TTM Squeeze,
        Aroon, Vortex, TRIX, Fibonacci retracement levels, Heikin-Ashi and the
        Chandelier Exit. Returns the latest reading for each.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return {}

        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=400)
        prices = self.price_repo.get_daily_prices(stock.id, start_date, end_date)
        if len(prices) < 30:
            return {}

        df = pd.DataFrame([{
            'date': p.timestamp,
            'open': float(p.open),
            'high': float(p.high),
            'low': float(p.low),
            'close': float(p.close),
            'volume': int(p.volume),
        } for p in prices]).set_index('date').sort_index()

        high, low, close, vol = df['high'], df['low'], df['close'], df['volume']
        last_close = float(close.iloc[-1])

        def _f(series, default=None):
            try:
                v = float(series.iloc[-1])
                return round(v, 4) if np.isfinite(v) else default
            except Exception:
                return default

        result: dict = {}

        # Parabolic SAR (0.02 step, 0.2 max)
        psar = ta.trend.PSARIndicator(high=high, low=low, close=close, step=0.02, max_step=0.2)
        sar_val = _f(psar.psar())
        result['parabolic_sar'] = {
            "value": sar_val,
            "trend": "BULLISH" if sar_val is not None and last_close > sar_val else "BEARISH",
        }

        # Donchian Channel (20)
        dc = ta.volatility.DonchianChannel(high=high, low=low, close=close, window=20)
        result['donchian'] = {
            "upper": _f(dc.donchian_channel_hband()),
            "middle": _f(dc.donchian_channel_mband()),
            "lower": _f(dc.donchian_channel_lband()),
        }

        # Keltner Channel (20, ATR mult 2)
        kc = ta.volatility.KeltnerChannel(high=high, low=low, close=close, window=20, window_atr=10, multiplier=2)
        kc_upper = _f(kc.keltner_channel_hband())
        kc_lower = _f(kc.keltner_channel_lband())
        result['keltner'] = {
            "upper": kc_upper,
            "middle": _f(kc.keltner_channel_mband()),
            "lower": kc_lower,
        }

        # Chaikin Money Flow (20)
        cmf_val = _f(ta.volume.ChaikinMoneyFlowIndicator(high=high, low=low, close=close, volume=vol, window=20).chaikin_money_flow())
        result['cmf'] = {
            "value": cmf_val,
            "signal": "ACCUMULATION" if cmf_val is not None and cmf_val > 0.05
                      else "DISTRIBUTION" if cmf_val is not None and cmf_val < -0.05 else "NEUTRAL",
        }

        # TTM Squeeze: squeeze ON when Bollinger Bands sit inside Keltner Channels.
        bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        bb_u, bb_l = _f(bb.bollinger_hband()), _f(bb.bollinger_lband())
        squeeze_on = (bb_u is not None and bb_l is not None and kc_upper is not None and kc_lower is not None
                      and bb_u < kc_upper and bb_l > kc_lower)
        # Momentum: linear-reg style proxy = close minus average of Donchian-mid and SMA20.
        sma20 = close.rolling(20).mean()
        dc_mid = (high.rolling(20).max() + low.rolling(20).min()) / 2
        mom_series = close - (dc_mid + sma20) / 2
        mom_val = _f(mom_series)
        result['ttm_squeeze'] = {
            "squeeze_on": bool(squeeze_on),
            "momentum": mom_val,
            "momentum_dir": "UP" if mom_val is not None and mom_val >= 0 else "DOWN",
            "state": "SQUEEZE (energy building)" if squeeze_on else "FIRED (expansion)",
        }

        # Aroon (25)
        aroon = ta.trend.AroonIndicator(high=high, low=low, window=25)
        aroon_up = _f(aroon.aroon_up())
        aroon_down = _f(aroon.aroon_down())
        result['aroon'] = {
            "up": aroon_up,
            "down": aroon_down,
            "oscillator": round(aroon_up - aroon_down, 2) if aroon_up is not None and aroon_down is not None else None,
        }

        # Vortex (14)
        vortex = ta.trend.VortexIndicator(high=high, low=low, close=close, window=14)
        vi_pos = _f(vortex.vortex_indicator_pos())
        vi_neg = _f(vortex.vortex_indicator_neg())
        result['vortex'] = {
            "vi_plus": vi_pos,
            "vi_minus": vi_neg,
            "signal": "BULLISH" if vi_pos is not None and vi_neg is not None and vi_pos > vi_neg else "BEARISH",
        }

        # TRIX (15)
        trix_series = ta.trend.TRIXIndicator(close=close, window=15).trix()
        trix_val = _f(trix_series)
        trix_prev = round(float(trix_series.iloc[-2]), 4) if len(trix_series) > 1 and np.isfinite(trix_series.iloc[-2]) else None
        result['trix'] = {
            "value": trix_val,
            "signal": "BULLISH" if trix_val is not None and trix_prev is not None and trix_val > trix_prev else "BEARISH",
        }

        # Fibonacci retracement from the last 60-bar swing high/low.
        window = close.iloc[-60:]
        swing_high = float(window.max())
        swing_low = float(window.min())
        rng = swing_high - swing_low
        uptrend = window.idxmax() > window.idxmin()  # high made after low -> measure retrace from high
        if rng > 0:
            if uptrend:
                levels = {f"{int(p*1000)/10}%": round(swing_high - rng * p, 2)
                          for p in (0.236, 0.382, 0.5, 0.618, 0.786)}
            else:
                levels = {f"{int(p*1000)/10}%": round(swing_low + rng * p, 2)
                          for p in (0.236, 0.382, 0.5, 0.618, 0.786)}
        else:
            levels = {}
        result['fibonacci'] = {
            "swing_high": round(swing_high, 2),
            "swing_low": round(swing_low, 2),
            "direction": "UPTREND" if uptrend else "DOWNTREND",
            "levels": levels,
            "ext_1272": round(swing_high + rng * 0.272, 2) if uptrend else round(swing_low - rng * 0.272, 2),
            "ext_1618": round(swing_high + rng * 0.618, 2) if uptrend else round(swing_low - rng * 0.618, 2),
        }

        # Heikin-Ashi (smoothed candles) + current colour streak.
        ha_close = (df['open'] + df['high'] + df['low'] + df['close']) / 4
        ha_open = pd.Series(index=df.index, dtype=float)
        ha_open.iloc[0] = (df['open'].iloc[0] + df['close'].iloc[0]) / 2
        for i in range(1, len(df)):
            ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2
        ha_bull = ha_close > ha_open
        streak = 1
        for i in range(len(ha_bull) - 2, -1, -1):
            if ha_bull.iloc[i] == ha_bull.iloc[-1]:
                streak += 1
            else:
                break
        result['heikin_ashi'] = {
            "open": round(float(ha_open.iloc[-1]), 2),
            "high": round(float(max(df['high'].iloc[-1], ha_open.iloc[-1], ha_close.iloc[-1])), 2),
            "low": round(float(min(df['low'].iloc[-1], ha_open.iloc[-1], ha_close.iloc[-1])), 2),
            "close": round(float(ha_close.iloc[-1]), 2),
            "color": "GREEN" if bool(ha_bull.iloc[-1]) else "RED",
            "streak": int(streak),
        }

        # Chandelier Exit (22-period, ATR mult 3).
        atr22 = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=22).average_true_range()
        atr_last = _f(atr22)
        hh22 = float(high.rolling(22).max().iloc[-1])
        ll22 = float(low.rolling(22).min().iloc[-1])
        if atr_last is not None:
            long_stop = round(hh22 - 3 * atr_last, 2)
            short_stop = round(ll22 + 3 * atr_last, 2)
        else:
            long_stop = short_stop = None
        result['chandelier_exit'] = {
            "long_stop": long_stop,
            "short_stop": short_stop,
            "bias": "LONG" if long_stop is not None and last_close > long_stop else "SHORT",
        }

        return {"symbol": symbol.upper(), "price": round(last_close, 2), "indicators": result}

