-- D1/D2/D3 data-semantics fix (see DATA_AUDIT_REPORT.md)
-- From the deploy date onward:
--   volume      = TRUE per-minute traded volume (from cumulative tick-volume diff)
--   day_volume  = cumulative day volume at that minute (new column)
--   open/high/low = TRUE per-minute bar OHLC (previously day-cumulative)
-- Data before the deploy date has the OLD semantics (volume = cumulative,
-- OHLC = day-running) and must NOT be mixed into training — training queries
-- are epoch-gated to trade_date >= 2026-07-07.

ALTER TABLE delta_minute_candle ADD COLUMN IF NOT EXISTS day_volume BIGINT;

COMMENT ON COLUMN delta_minute_candle.volume IS
  'Per-minute traded volume since 2026-07-07 (cumulative day volume before that date)';
COMMENT ON COLUMN delta_minute_candle.day_volume IS
  'Cumulative day volume at this minute (populated from 2026-07-07)';
