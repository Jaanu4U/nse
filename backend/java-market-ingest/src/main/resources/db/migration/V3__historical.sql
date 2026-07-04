-- Historical daily price bars loaded from Kite (3 months backfill)
CREATE TABLE IF NOT EXISTS delta_prices_daily (
    id          BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(20)   NOT NULL,
    trade_date  DATE          NOT NULL,
    open_price  NUMERIC(14,4) NOT NULL,
    high_price  NUMERIC(14,4) NOT NULL,
    low_price   NUMERIC(14,4) NOT NULL,
    close_price NUMERIC(14,4) NOT NULL,
    volume      BIGINT        NOT NULL,
    source      VARCHAR(10)   NOT NULL DEFAULT 'KITE', -- KITE | YFINANCE
    loaded_at   TIMESTAMPTZ   DEFAULT NOW(),
    CONSTRAINT uq_dpd_symbol_date UNIQUE (symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_dpd_symbol_date ON delta_prices_daily (symbol, trade_date);
CREATE INDEX IF NOT EXISTS idx_dpd_date        ON delta_prices_daily (trade_date);
