-- EOD prediction accuracy tracking table (PREDICTION_FLOW_AUDIT.md — roadmap item)
-- Populated nightly at 16:00 IST by the Python scheduler eod_accuracy_job.
-- Records directional hit-rate, calibration and profit factor so the feedback
-- loop from prediction → realised outcome is closed.

CREATE TABLE IF NOT EXISTS delta_prediction_accuracy (
    id                 BIGSERIAL    PRIMARY KEY,
    trade_date         DATE         NOT NULL,
    total_predictions  INTEGER      NOT NULL DEFAULT 0,
    hits               INTEGER      NOT NULL DEFAULT 0,
    hit_rate           NUMERIC(5,2),          -- % correct directional calls
    avg_predicted_score NUMERIC(6,2),         -- mean prediction_score of all calls
    avg_realised_move  NUMERIC(8,4),          -- mean 15-min % move after signal
    profit_factor      NUMERIC(8,3),          -- sum(winning moves) / sum(losing moves)
    computed_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dpa_date UNIQUE (trade_date)
);
CREATE INDEX IF NOT EXISTS idx_dpa_date ON delta_prediction_accuracy (trade_date DESC);
