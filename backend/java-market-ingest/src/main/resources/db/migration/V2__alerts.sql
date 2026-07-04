-- Delta Alert System tables

CREATE TABLE IF NOT EXISTS delta_alert_rules (
    id         BIGSERIAL PRIMARY KEY,
    symbol     VARCHAR(20)  NOT NULL,
    type       VARCHAR(40)  NOT NULL,
    threshold  NUMERIC(18,4) NOT NULL,
    once       BOOLEAN      NOT NULL DEFAULT FALSE,
    active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dar_symbol ON delta_alert_rules (symbol, active);

CREATE TABLE IF NOT EXISTS delta_alert_fired (
    id               BIGSERIAL PRIMARY KEY,
    rule_id          BIGINT,
    symbol           VARCHAR(20)  NOT NULL,
    type             VARCHAR(40)  NOT NULL,
    triggered_value  NUMERIC(18,4),
    threshold        NUMERIC(18,4),
    fired_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_daf_symbol  ON delta_alert_fired (symbol);
CREATE INDEX IF NOT EXISTS idx_daf_fired   ON delta_alert_fired (fired_at);
