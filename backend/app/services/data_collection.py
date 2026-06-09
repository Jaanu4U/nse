import logging
import requests
import io
import csv
import pandas as pd
import yfinance as yf
import datetime
import time
from sqlalchemy.orm import Session
from app.repositories.stock_repo import StockRepository
from app.repositories.price_repo import PriceRepository
from typing import List, Optional

logger = logging.getLogger(__name__)

# Pre-defined list of Nifty 50 stocks for offline / fallback sync
FALLBACK_STOCKS = [
    {"symbol": "RELIANCE", "company_name": "Reliance Industries Limited", "industry": "Oil Gas & Fuels"},
    {"symbol": "TCS", "company_name": "Tata Consultancy Services Limited", "industry": "Information Technology"},
    {"symbol": "INFY", "company_name": "Infosys Limited", "industry": "Information Technology"},
    {"symbol": "HDFCBANK", "company_name": "HDFCBANK Limited", "industry": "Financial Services"},
    {"symbol": "ICICIBANK", "company_name": "ICICI Bank Limited", "industry": "Financial Services"},
    {"symbol": "BHARTIARTL", "company_name": "Bharti Airtel Limited", "industry": "Telecommunication"},
    {"symbol": "SBIN", "company_name": "State Bank of India", "industry": "Financial Services"},
    {"symbol": "ITC", "company_name": "ITC Limited", "industry": "Fast Moving Consumer Goods"},
    {"symbol": "HINDUNILVR", "company_name": "Hindustan Unilever Limited", "industry": "Fast Moving Consumer Goods"},
    {"symbol": "LT", "company_name": "Larsen & Toubro Limited", "industry": "Construction"},
    {"symbol": "BAJFINANCE", "company_name": "Bajaj Finance Limited", "industry": "Financial Services"},
    {"symbol": "KOTAKBANK", "company_name": "Kotak Mahindra Bank Limited", "industry": "Financial Services"},
    {"symbol": "HCLTECH", "company_name": "HCL Technologies Limited", "industry": "Information Technology"},
    {"symbol": "AXISBANK", "company_name": "Axis Bank Limited", "industry": "Financial Services"},
    {"symbol": "MARUTI", "company_name": "Maruti Suzuki India Limited", "industry": "Automobile"},
    {"symbol": "SUNPHARMA", "company_name": "Sun Pharmaceutical Industries Limited", "industry": "Healthcare"},
    {"symbol": "TATAMOTORS", "company_name": "Tata Motors Limited", "industry": "Automobile"},
    {"symbol": "TITAN", "company_name": "Titan Company Limited", "industry": "Consumer Durables"},
    {"symbol": "TATASTEEL", "company_name": "Tata Steel Limited", "industry": "Metals & Mining"},
    {"symbol": "WIPRO", "company_name": "Wipro Limited", "industry": "Information Technology"},
]

class DataCollectionEngine:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)

    def sync_stock_master(self) -> int:
        """
        Download the NSE equity stock list CSV and populate/sync the stock master table.
        If network fails, falls back to pre-defined main tickers.
        """
        url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        logger.info("Attempting to sync NSE stock master list...")
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            })
            # Hit homepage to get session cookies
            session.get("https://www.nseindia.com", timeout=10)
            response = session.get(url, timeout=15)
            response.raise_for_status()
            
            # Read CSV content
            csv_data = response.content.decode('utf-8')
            csv_reader = csv.DictReader(io.StringIO(csv_data))
            
            stocks_to_sync = []
            for row in csv_reader:
                # Standardize keys by stripping whitespaces
                row = {k.strip(): v for k, v in row.items() if k is not None}
                symbol = row.get("SYMBOL", "").strip()
                company_name = row.get("NAME OF COMPANY", "").strip()
                series = row.get("SERIES", "").strip()
                isin = (row.get("ISIN NUMBER") or row.get("ISIN") or "").strip()
                
                # We only track standard equities ('EQ' series) to avoid warrants, bonds, etc.
                if symbol and series == "EQ":
                    stocks_to_sync.append({
                        "symbol": symbol,
                        "company_name": company_name,
                        "series": series,
                        "isin": isin,
                        "industry": None,  # To be populated later or from external scraping
                        "is_active": True
                    })
            
            if stocks_to_sync:
                self.stock_repo.bulk_create_or_update(stocks_to_sync)
                logger.info(f"Successfully synced {len(stocks_to_sync)} stocks from NSE.")
                return len(stocks_to_sync)
                
        except Exception as e:
            logger.warning(f"Failed to fetch NSE stock list from URL: {e}. Falling back to default lists.")
            
        # Fallback to local subset
        fallback_data = [
            {
                "symbol": s["symbol"],
                "company_name": s["company_name"],
                "series": "EQ",
                "isin": None,
                "industry": s["industry"],
                "is_active": True
            } for s in FALLBACK_STOCKS
        ]
        self.stock_repo.bulk_create_or_update(fallback_data)
        logger.info(f"Synced {len(fallback_data)} fallback stocks.")
        return len(fallback_data)

    def download_historical_ohlcv(self, symbol: str, start_date: datetime.date, end_date: datetime.date, retry_count: int = 3) -> int:
        """
        Download historical daily data for a symbol via yfinance and upsert to database.
        """
        yf_symbol = f"{symbol}.NS"
        logger.info(f"Downloading historical daily data for {symbol} ({start_date} to {end_date})")
        
        # Load stock record to get ID
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            logger.error(f"Stock {symbol} not found in database. Sync stock master first.")
            return 0
            
        for attempt in range(retry_count):
            try:
                # yfinance fetch
                df = yf.download(yf_symbol, start=start_date, end=end_date, progress=False)
                if df.empty:
                    logger.warning(f"No data returned for {symbol} between {start_date} and {end_date}")
                    return 0
                
                prices_list = []
                for idx, row in df.iterrows():
                    # Handle single/multi-index column format returned by newer yfinance versions
                    try:
                        open_val = float(row['Open'])
                        high_val = float(row['High'])
                        low_val = float(row['Low'])
                        close_val = float(row['Close'])
                        volume_val = int(row['Volume'])
                        adj_close_val = float(row.get('Adj Close', close_val))
                    except (KeyError, ValueError, TypeError) as conv_err:
                        # Sometimes columns are tuples: ('Open', 'RELIANCE.NS')
                        open_val = float(row[('Open', yf_symbol)])
                        high_val = float(row[('High', yf_symbol)])
                        low_val = float(row[('Low', yf_symbol)])
                        close_val = float(row[('Close', yf_symbol)])
                        volume_val = int(row[('Volume', yf_symbol)])
                        adj_close_val = float(row.get(('Adj Close', yf_symbol), close_val))

                    # Skip invalid rows
                    if pd.isna(open_val) or pd.isna(close_val):
                        continue
                        
                    prices_list.append({
                        "stock_id": stock.id,
                        "timestamp": idx.date(),
                        "open": open_val,
                        "high": high_val,
                        "low": low_val,
                        "close": close_val,
                        "volume": volume_val,
                        "adj_close": adj_close_val
                    })
                
                if prices_list:
                    self.price_repo.bulk_upsert_daily(prices_list)
                    logger.info(f"Saved {len(prices_list)} daily candles for {symbol}.")
                    return len(prices_list)
                return 0
                
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed for {symbol}: {e}")
                time.sleep(1.5 * (attempt + 1))
                
        return 0

    def download_intraday_ohlcv(self, symbol: str, interval: str = "15m", period: str = "5d") -> int:
        """
        Download intraday data (e.g. 5m, 15m, 1h) via yfinance.
        """
        yf_symbol = f"{symbol}.NS"
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            return 0
            
        try:
            df = yf.download(yf_symbol, period=period, interval=interval, progress=False)
            if df.empty:
                return 0
                
            intraday_list = []
            for idx, row in df.iterrows():
                try:
                    open_val = float(row['Open'])
                    high_val = float(row['High'])
                    low_val = float(row['Low'])
                    close_val = float(row['Close'])
                    volume_val = int(row['Volume'])
                except (KeyError, ValueError, TypeError):
                    open_val = float(row[('Open', yf_symbol)])
                    high_val = float(row[('High', yf_symbol)])
                    low_val = float(row[('Low', yf_symbol)])
                    close_val = float(row[('Close', yf_symbol)])
                    volume_val = int(row[('Volume', yf_symbol)])

                if pd.isna(open_val) or pd.isna(close_val):
                    continue

                intraday_list.append({
                    "stock_id": stock.id,
                    "timestamp": idx.to_pydatetime(),
                    "open": open_val,
                    "high": high_val,
                    "low": low_val,
                    "close": close_val,
                    "volume": volume_val,
                    "interval": interval
                })
                
            if intraday_list:
                self.price_repo.bulk_upsert_intraday(intraday_list)
                logger.info(f"Saved {len(intraday_list)} intraday candles ({interval}) for {symbol}.")
                return len(intraday_list)
                
        except Exception as e:
            logger.error(f"Failed to download intraday data for {symbol}: {e}")
            
        return 0

    def incremental_update(self) -> int:
        """
        For each active stock in database, detect the latest price date, and download prices up to today.
        """
        active_stocks = self.stock_repo.get_active_stocks()
        total_candles = 0
        today = datetime.date.today()
        
        logger.info(f"Starting incremental update for {len(active_stocks)} stocks...")
        for stock in active_stocks:
            latest_price = self.price_repo.get_latest_daily_price(stock.id)
            if latest_price:
                start_date = latest_price.timestamp + datetime.timedelta(days=1)
            else:
                # Default to last 3 years if empty
                start_date = today - datetime.timedelta(days=365 * 3)
                
            # If start_date is in the future or today, check if it's weekend, else download
            if start_date <= today:
                candles = self.download_historical_ohlcv(stock.symbol, start_date, today + datetime.timedelta(days=1))
                total_candles += candles
                
                # Also pull intraday data
                self.download_intraday_ohlcv(stock.symbol, interval="15m", period="5d")
                
        logger.info(f"Incremental update complete. Ingested {total_candles} daily candles total.")
        return total_candles
