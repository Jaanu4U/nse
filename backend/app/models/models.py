from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Numeric, BigInteger, ForeignKey, Table, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base
import datetime

# Secondary table for Watchlist Items (Many-to-Many between Watchlist and Stock)
watchlist_items = Table(
    "watchlist_items",
    Base.metadata,
    Column("watchlist_id", Integer, ForeignKey("watchlists.id", ondelete="CASCADE"), primary_key=True),
    Column("stock_id", Integer, ForeignKey("stocks.id", ondelete="CASCADE"), primary_key=True),
)

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    watchlists = relationship("Watchlist", back_populates="user", cascade="all, delete-orphan")
    portfolios = relationship("Portfolio", back_populates="user", cascade="all, delete-orphan")


class Stock(Base):
    __tablename__ = "stocks"
    
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), unique=True, nullable=False, index=True)
    company_name = Column(String(255), nullable=False)
    series = Column(String(10), default="EQ", nullable=False)
    isin = Column(String(20), unique=True, nullable=True)
    industry = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    prices_daily = relationship("PriceDaily", back_populates="stock", cascade="all, delete-orphan")
    prices_intraday = relationship("PriceIntraday", back_populates="stock", cascade="all, delete-orphan")
    technical_indicators = relationship("TechnicalIndicator", back_populates="stock", cascade="all, delete-orphan")
    corporate_actions = relationship("CorporateAction", back_populates="stock", cascade="all, delete-orphan")
    news = relationship("News", back_populates="stock", cascade="all, delete-orphan")
    predictions = relationship("Prediction", back_populates="stock", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="stock", cascade="all, delete-orphan")


class PriceDaily(Base):
    __tablename__ = "prices_daily"
    
    # Composite PK matches our partition strategy
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), primary_key=True)
    timestamp = Column(Date, primary_key=True)
    open = Column(Numeric(12, 2), nullable=False)
    high = Column(Numeric(12, 2), nullable=False)
    low = Column(Numeric(12, 2), nullable=False)
    close = Column(Numeric(12, 2), nullable=False)
    volume = Column(BigInteger, nullable=False)
    adj_close = Column(Numeric(12, 2), nullable=False)
    
    stock = relationship("Stock", back_populates="prices_daily")


class PriceIntraday(Base):
    __tablename__ = "prices_intraday"
    
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), primary_key=True)
    timestamp = Column(DateTime(timezone=True), primary_key=True)
    open = Column(Numeric(12, 2), nullable=False)
    high = Column(Numeric(12, 2), nullable=False)
    low = Column(Numeric(12, 2), nullable=False)
    close = Column(Numeric(12, 2), nullable=False)
    volume = Column(BigInteger, nullable=False)
    interval = Column(String(10), nullable=False) # '1m', '5m', '15m', '1h'
    
    stock = relationship("Stock", back_populates="prices_intraday")


class TechnicalIndicator(Base):
    __tablename__ = "technical_indicators"
    
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), primary_key=True)
    timestamp = Column(Date, primary_key=True)
    
    rsi = Column(Numeric(6, 2), nullable=True)
    macd = Column(Numeric(12, 4), nullable=True)
    macd_signal = Column(Numeric(12, 4), nullable=True)
    macd_hist = Column(Numeric(12, 4), nullable=True)
    ema_20 = Column(Numeric(12, 2), nullable=True)
    ema_50 = Column(Numeric(12, 2), nullable=True)
    ema_200 = Column(Numeric(12, 2), nullable=True)
    sma_20 = Column(Numeric(12, 2), nullable=True)
    bb_upper = Column(Numeric(12, 2), nullable=True)
    bb_middle = Column(Numeric(12, 2), nullable=True)
    bb_lower = Column(Numeric(12, 2), nullable=True)
    vwap = Column(Numeric(12, 2), nullable=True)
    atr = Column(Numeric(12, 2), nullable=True)
    adx = Column(Numeric(6, 2), nullable=True)
    ichimoku_tenkan = Column(Numeric(12, 2), nullable=True)
    ichimoku_kijun = Column(Numeric(12, 2), nullable=True)
    ichimoku_senkou_a = Column(Numeric(12, 2), nullable=True)
    ichimoku_senkou_b = Column(Numeric(12, 2), nullable=True)
    
    stock = relationship("Stock", back_populates="technical_indicators")


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    
    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    action_type = Column(String(20), nullable=False) # 'DIVIDEND', 'SPLIT', 'BONUS', 'RIGHTS'
    execution_date = Column(Date, nullable=False)
    ratio_from = Column(Numeric(10, 4), nullable=True)
    ratio_to = Column(Numeric(10, 4), nullable=True)
    amount = Column(Numeric(10, 2), nullable=True)
    is_applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    stock = relationship("Stock", back_populates="corporate_actions")


class News(Base):
    __tablename__ = "news"
    
    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(500), nullable=False)
    url = Column(String(500), unique=True, nullable=False)
    source = Column(String(100), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False, index=True)
    content = Column(String, nullable=True)
    sentiment_score = Column(Numeric(4, 2), nullable=True) # -1.0 to 1.0
    sentiment_class = Column(String(10), nullable=True) # 'POSITIVE', 'NEUTRAL', 'NEGATIVE'
    
    stock = relationship("Stock", back_populates="news")


class Prediction(Base):
    __tablename__ = "predictions"
    
    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(Date, nullable=False)
    prob_plus_1 = Column(Numeric(5, 4), nullable=False)
    prob_plus_2 = Column(Numeric(5, 4), nullable=False)
    prob_plus_3 = Column(Numeric(5, 4), nullable=False)
    prob_plus_5 = Column(Numeric(5, 4), nullable=False)
    model_version = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    stock = relationship("Stock", back_populates="predictions")


class Watchlist(Base):
    __tablename__ = "watchlists"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    user = relationship("User", back_populates="watchlists")
    stocks = relationship("Stock", secondary=watchlist_items)


class Portfolio(Base):
    __tablename__ = "portfolios"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    user = relationship("User", back_populates="portfolios")
    transactions = relationship("Transaction", back_populates="portfolio", cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    transaction_type = Column(String(10), nullable=False) # 'BUY', 'SELL'
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(12, 2), nullable=False)
    transaction_date = Column(Date, nullable=False)
    charges = Column(Numeric(8, 2), default=0.00)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    portfolio = relationship("Portfolio", back_populates="transactions")
    stock = relationship("Stock", back_populates="transactions")


class DetectedPattern(Base):
    __tablename__ = "detected_patterns"
    
    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(Date, nullable=False)
    pattern_name = Column(String(50), nullable=False) # 'DOUBLE_BOTTOM', 'BULLISH_ENGULFING', etc.
    signal_type = Column(String(10), nullable=False)  # 'BULLISH', 'BEARISH'
    confidence = Column(Numeric(5, 2), nullable=False) # Score out of 100
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    stock = relationship("Stock")


class Financial(Base):
    __tablename__ = "financials"
    
    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    fiscal_year = Column(Integer, nullable=False)
    revenue = Column(Numeric(15, 2), nullable=True)
    net_income = Column(Numeric(15, 2), nullable=True)
    eps = Column(Numeric(10, 2), nullable=True)
    pe = Column(Numeric(8, 2), nullable=True)
    pb = Column(Numeric(8, 2), nullable=True)
    roe = Column(Numeric(5, 2), nullable=True) # Percentage
    roce = Column(Numeric(5, 2), nullable=True) # Percentage
    debt_to_equity = Column(Numeric(8, 4), nullable=True)
    free_cash_flow = Column(Numeric(15, 2), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    stock = relationship("Stock")


class QuarterlyResult(Base):
    __tablename__ = "quarterly_results"
    
    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    quarter = Column(String(10), nullable=False) # '2025-Q1', '2025-Q2'
    revenue = Column(Numeric(15, 2), nullable=True)
    net_income = Column(Numeric(15, 2), nullable=True)
    eps = Column(Numeric(10, 2), nullable=True)
    operating_profit_margin = Column(Numeric(5, 2), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    stock = relationship("Stock")


class FIIDIIActivity(Base):
    __tablename__ = "fii_dii_activities"
    
    timestamp = Column(Date, primary_key=True)
    fii_cash_net = Column(Numeric(15, 2), nullable=False) # Net cash inflow/outflow in Crores (INR)
    dii_cash_net = Column(Numeric(15, 2), nullable=False)
    fii_index_futures_net = Column(Numeric(15, 2), nullable=True)
    fii_index_options_net = Column(Numeric(15, 2), nullable=True)
    fii_stock_futures_net = Column(Numeric(15, 2), nullable=True)
    fii_stock_options_net = Column(Numeric(15, 2), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class InsiderTrade(Base):
    __tablename__ = "insider_trades"
    
    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    acquirer_name = Column(String(255), nullable=False)
    category = Column(String(100), nullable=False)  # 'PROMOTER', 'DIRECTOR', etc.
    transaction_type = Column(String(20), nullable=False)  # 'BUY', 'SELL'
    quantity = Column(BigInteger, nullable=False)
    value = Column(Numeric(15, 2), nullable=False)  # Value in INR
    transaction_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    stock = relationship("Stock")


class OptionChainMetric(Base):
    __tablename__ = "option_chain_metrics"
    
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), primary_key=True)
    timestamp = Column(Date, primary_key=True)
    pcr = Column(Numeric(6, 3), nullable=False)  # Put-Call Ratio
    max_pain = Column(Numeric(12, 2), nullable=False)  # Strike price representing Max Pain
    total_call_oi = Column(BigInteger, nullable=False)
    total_put_oi = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    stock = relationship("Stock")



