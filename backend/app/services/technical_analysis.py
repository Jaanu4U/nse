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
        if len(prices) < 14:
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

        # 5. VWAP (Volume Weighted Average Price)
        # For daily charts, cumulative VWAP since the start of dataset is calculated
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()

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
                "ichimoku_senkou_b": row['ichimoku_senkou_b']
            })

        if indicators_to_save:
            self.indicator_repo.bulk_upsert(indicators_to_save)
            logger.info(f"Calculated and saved {len(indicators_to_save)} indicators for {symbol}.")
            return len(indicators_to_save)
            
        return 0

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
