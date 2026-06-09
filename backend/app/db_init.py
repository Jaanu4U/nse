import logging
from sqlalchemy import text
from app.database import engine, Base
from app.models import Base as ModelsBase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_database():
    logger.info("Initializing database schemas and partitioning...")
    
    with engine.connect() as conn:
        is_postgres = engine.dialect.name == "postgresql"
        
        # Check if stocks table exists. if not, we build core tables
        # Let's create stocks first since partition tables refer to it
        if is_postgres:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS stocks (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) UNIQUE NOT NULL,
                    company_name VARCHAR(255) NOT NULL,
                    series VARCHAR(10) NOT NULL DEFAULT 'EQ',
                    isin VARCHAR(20) UNIQUE,
                    industry VARCHAR(100),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_stocks_symbol ON stocks(symbol);
            """))
        else:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol VARCHAR(20) UNIQUE NOT NULL,
                    company_name VARCHAR(255) NOT NULL,
                    series VARCHAR(10) NOT NULL DEFAULT 'EQ',
                    isin VARCHAR(20) UNIQUE,
                    industry VARCHAR(100),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_stocks_symbol ON stocks(symbol);
            """))
        
        if is_postgres:
            # Check if prices_daily exists in Postgres
            res = conn.execute(text("SELECT to_regclass('prices_daily');"))
            if res.scalar() is None:
                logger.info("Creating partitioned table prices_daily...")
                conn.execute(text("""
                    CREATE TABLE prices_daily (
                        stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                        timestamp DATE NOT NULL,
                        open DECIMAL(12, 2) NOT NULL,
                        high DECIMAL(12, 2) NOT NULL,
                        low DECIMAL(12, 2) NOT NULL,
                        close DECIMAL(12, 2) NOT NULL,
                        volume BIGINT NOT NULL,
                        adj_close DECIMAL(12, 2) NOT NULL,
                        PRIMARY KEY (stock_id, timestamp)
                    ) PARTITION BY RANGE (timestamp);
                    
                    CREATE TABLE prices_daily_y2024 PARTITION OF prices_daily
                        FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
                    CREATE TABLE prices_daily_y2025 PARTITION OF prices_daily
                        FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
                    CREATE TABLE prices_daily_y2026 PARTITION OF prices_daily
                        FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
                    CREATE TABLE prices_daily_y2027 PARTITION OF prices_daily
                        FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
                    CREATE TABLE prices_daily_default PARTITION OF prices_daily DEFAULT;
                """))
                
            # Check if prices_intraday exists in Postgres
            res = conn.execute(text("SELECT to_regclass('prices_intraday');"))
            if res.scalar() is None:
                logger.info("Creating partitioned table prices_intraday...")
                conn.execute(text("""
                    CREATE TABLE prices_intraday (
                        stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                        timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                        open DECIMAL(12, 2) NOT NULL,
                        high DECIMAL(12, 2) NOT NULL,
                        low DECIMAL(12, 2) NOT NULL,
                        close DECIMAL(12, 2) NOT NULL,
                        volume BIGINT NOT NULL,
                        interval VARCHAR(10) NOT NULL,
                        PRIMARY KEY (stock_id, timestamp)
                    ) PARTITION BY RANGE (timestamp);
                    
                    CREATE TABLE prices_intraday_y2024 PARTITION OF prices_intraday
                        FOR VALUES FROM ('2024-01-01 00:00:00+00') TO ('2025-01-01 00:00:00+00');
                    CREATE TABLE prices_intraday_y2025 PARTITION OF prices_intraday
                        FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2026-01-01 00:00:00+00');
                    CREATE TABLE prices_intraday_y2026 PARTITION OF prices_intraday
                        FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2027-01-01 00:00:00+00');
                    CREATE TABLE prices_intraday_y2027 PARTITION OF prices_intraday
                        FOR VALUES FROM ('2027-01-01 00:00:00+00') TO ('2028-01-01 00:00:00+00');
                    CREATE TABLE prices_intraday_default PARTITION OF prices_intraday DEFAULT;
                """))
        else:
            # SQLite fallback for local testing
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS prices_daily (
                    stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                    timestamp DATE NOT NULL,
                    open DECIMAL(12, 2) NOT NULL,
                    high DECIMAL(12, 2) NOT NULL,
                    low DECIMAL(12, 2) NOT NULL,
                    close DECIMAL(12, 2) NOT NULL,
                    volume BIGINT NOT NULL,
                    adj_close DECIMAL(12, 2) NOT NULL,
                    PRIMARY KEY (stock_id, timestamp)
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS prices_intraday (
                    stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                    timestamp DATETIME NOT NULL,
                    open DECIMAL(12, 2) NOT NULL,
                    high DECIMAL(12, 2) NOT NULL,
                    low DECIMAL(12, 2) NOT NULL,
                    close DECIMAL(12, 2) NOT NULL,
                    volume BIGINT NOT NULL,
                    interval VARCHAR(10) NOT NULL,
                    PRIMARY KEY (stock_id, timestamp)
                );
            """))
        conn.commit()

    # Create the rest of the metadata tables (users, watchlists, technical_indicators, news, etc.)
    logger.info("Creating standard metadata tables...")
    ModelsBase.metadata.create_all(bind=engine)
    logger.info("Database initialization completed successfully.")

if __name__ == "__main__":
    init_database()
