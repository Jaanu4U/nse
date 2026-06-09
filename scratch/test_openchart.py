import sys
import os
from datetime import datetime, timedelta

# Create virtual environment packages check or import fallback warning
try:
    from openchart import NSEData
except ImportError:
    print("Error: 'openchart' library is not installed. Please run: pip install openchart")
    sys.exit(1)

def test_fetch_stock_eod(symbol: str = "RELIANCE"):
    print(f"Initializing OpenChart client to fetch EOD data for {symbol}...")
    try:
        nse = NSEData()
        
        # Define 30 days query period
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=30)
        
        # Fetch EOD (daily '1d') candles for Equity ('EQ')
        df = nse.historical(symbol, 'EQ', start_dt, end_dt, '1d')
        
        if df is not None and not df.empty:
            print("\nSuccessfully fetched historical data!")
            print(f"Total rows: {len(df)}")
            print("\nFirst 5 candles:")
            print(df.head())
            print("\nLast 5 candles:")
            print(df.tail())
        else:
            print(f"Failed: No EOD data returned for {symbol}.")
            
    except Exception as e:
        print(f"Error executing OpenChart query: {e}")

if __name__ == "__main__":
    # Test with RELIANCE as default
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    test_fetch_stock_eod(ticker)
