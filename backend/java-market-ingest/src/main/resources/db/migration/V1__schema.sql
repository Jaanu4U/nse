-- NSE Delta System – PostgreSQL DDL (Flyway V1)
-- All tables prefixed with delta_ to avoid conflicts with Python backend tables.
-- The stocks table is READ-ONLY from this service (owned by Python backend).

CREATE TABLE IF NOT EXISTS delta_daily_summary (
    id               BIGSERIAL PRIMARY KEY,
    trade_date       DATE          NOT NULL,
    symbol           VARCHAR(20)   NOT NULL,
    open_price       NUMERIC(14,4),
    high_price       NUMERIC(14,4),
    low_price        NUMERIC(14,4),
    close_price      NUMERIC(14,4),
    vwap             NUMERIC(18,6),
    volume           BIGINT,
    trades           INTEGER,
    buy_volume       BIGINT,
    sell_volume      BIGINT,
    delta            BIGINT,
    cumulative_delta BIGINT,
    delivery_pct     NUMERIC(7,4),
    prediction_score NUMERIC(7,4),
    bullish_prob     NUMERIC(7,4),
    bearish_prob     NUMERIC(7,4),
    confidence_score NUMERIC(7,4),
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_delta_daily UNIQUE (trade_date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_dds_symbol ON delta_daily_summary (symbol);
CREATE INDEX IF NOT EXISTS idx_dds_date   ON delta_daily_summary (trade_date);

CREATE TABLE IF NOT EXISTS delta_minute_candle (
    id           BIGSERIAL PRIMARY KEY,
    symbol       VARCHAR(20)  NOT NULL,
    trade_date   DATE         NOT NULL,
    minute_ts    TIMESTAMPTZ  NOT NULL,
    open_price   NUMERIC(14,4),
    high_price   NUMERIC(14,4),
    low_price    NUMERIC(14,4),
    close_price  NUMERIC(14,4),
    volume       BIGINT,
    buy_volume   BIGINT,
    sell_volume  BIGINT,
    delta        BIGINT
);

CREATE INDEX IF NOT EXISTS idx_dmc_symbol_date ON delta_minute_candle (symbol, trade_date);
CREATE INDEX IF NOT EXISTS idx_dmc_ts          ON delta_minute_candle (minute_ts);

CREATE TABLE IF NOT EXISTS delta_prediction_history (
    id               BIGSERIAL PRIMARY KEY,
    symbol           VARCHAR(20)  NOT NULL,
    trade_date       DATE         NOT NULL,
    scored_at        TIMESTAMPTZ  NOT NULL,
    prediction_score NUMERIC(7,4),
    bullish_prob     NUMERIC(7,4),
    bearish_prob     NUMERIC(7,4),
    confidence_score NUMERIC(7,4),
    delta_input      BIGINT,
    cum_delta_input  BIGINT,
    vwap_input       NUMERIC(14,4),
    volume_input     BIGINT,
    rsi_input        NUMERIC(7,4),
    atr_input        NUMERIC(10,4)
);

CREATE INDEX IF NOT EXISTS idx_dph_symbol ON delta_prediction_history (symbol, trade_date);
