# Prediction Data Flow Audit — Mistakes & Improvements

**Date:** 2026-07-06 | **System:** NSE Intraday Delta AI (Level 1 + 2 + 3 live)

---

## Data Flow Overview (current)

```
Kite WebSocket ticks
  → LeeReadyClassifier (buy/sell)
  → DeltaEngine (delta, cumulative delta)          [in-memory, per symbol]
  → SymbolState (VWAP, OHLC, RSI, volume ratios)
  → MinuteCandleAggregator → delta_minute_candle   [DB, 1-min bars]
                                   │
        train_delta_models.py (weekly, Sun 02:00)
        ├─ Level 2 → delta_stock_profiles (impact_coeff, up_prob)
        └─ Level 3 → XGBoost → delta_ml_predictions (ml_up_prob)
                                   │
        MarketScheduler.loadProfiles() (startup / 09:00)
                                   │
  PredictionEngine.score()  =  L1 formula (70%) + L2/L3 blend (30%)
        + regime multiplier (VIX / expiry / gap)
        + expectedMove = deltaStrength × impactCoeff × confirmMult
                                   │
  SSE stream → dashboard | PaperTradingEngine (entry/exit)
```

---

## CRITICAL ISSUES (must fix)

### C1. Paper trading entry conditions were mathematically impossible — FIXED
- Entry required histProb >= 52 OR mlProb >= 0.52.
- Reality: max up_prob_overall in DB = 51.9 (0 symbols >= 52); max ml_up_prob
  = 0.343 (0 symbols >= 0.52). Zero trades would ever fire.
- Fix applied (2026-07-06): thresholds recalibrated to actual distributions —
  histProb >= 50 (13 symbols pass) OR mlProb >= 0.28 (72 symbols pass).
  Combined with score >= 65 + deltaStr >= 2% it stays selective.
- Better long-term: use percentile ranks (top 20% of universe today), not
  absolute cutoffs.

### C2. ML probability compared to the wrong midpoint (0.5)
- PredictionEngine blends with probSignal = (blended - 0.5) x 4.
- XGBoost target base rate is only 14.9%; average prediction 0.22.
  A prob of 0.30 is actually BULLISH (2x base rate) but treated as strongly
  bearish.
- Effect: the L2/L3 blend systematically drags every score down.
- Fix: normalize against base rate, not 0.5:
  mlSignal   = (mlProb - baseRate) / baseRate     // (0.30-0.15)/0.15 = +1.0
  histSignal = (histProb - 46.0) / 10.0           // 46 = universe mean
  Save base_rate inside the model pickle at training time; load with model.

### C3. Level 2 impact_coeff computed from VOLUME ratio, not DELTA strength
- Roadmap: Impact Coefficient = Future Move % / Delta Strength %.
- Actual SQL: REGR_SLOPE(move_15m, vol_ratio) — unsigned volume has no
  direction, so the slope sign is arbitrary noise.
- expectedMove = deltaStrength x impactCoeff → wrong direction ~50% of time.
- Fix (delta column already exists in delta_minute_candle):
  delta_strength = delta::float / NULLIF(avg_same_time_vol,0) * 100
  impact_coeff   = REGR_SLOPE(move_15m, delta_strength)  -- signed vs signed
  WHERE ABS(delta_strength) >= 0.5                       -- drop noise rows

### C4. Level 2 up_prob_overall is unconditional (no signal filter)
- Currently: "how often did the stock rise in ANY 15-min window" → ~46% for
  everything (market drift, zero edge).
- Roadmap: conditional probability given delta-strength bucket + VWAP state.
- Fix: per-bucket conditional up-probs keyed on delta-strength buckets
  (weak/normal/strong/very-strong), min 25-30 samples per bucket; at runtime
  look up the bucket matching live delta strength.

### C5. Level 3 features contain NO delta features
- FEATURE_COLS = time, price vs open, candle body, wicks, vol_ratio,
  prev_1m_move. buy_volume/sell_volume/delta exist in DB but are UNUSED.
- Top feature is ist_hour (57%) → model mostly learned time-of-day drift.
- Fix: add delta_pct, delta_strength, cum_delta_slope (5-15 min),
  vwap_distance_pct — the core signals of the entire system.

### C6. ML predictions are stale (scored on last candle of previous session)
- generate_ml_predictions uses each symbol's most recent stored candle
  (Fri 15:29) with ist_hour=15; result is static all next day/week.
- Minimum fix: regenerate daily at 09:00 with full-previous-day aggregate
  features. Proper fix: live per-minute scoring (ONNX in Java, or a small
  POST /internal/ml-score endpoint on the Python backend, cached 1 min).

---

## MODERATE ISSUES

### M1. getDelta() semantics change after every minute flush
- MinuteCandleAggregator calls snapshotAndResetRate() → buy/sell adders reset
  each minute. So getDelta() = minute delta, while VWAP/totalVolume/
  cumulativeDelta are full-day.
- Consequences: SSE delta is per-minute under a day-sounding label;
  getDeltaPercent() divides minute delta by day volume (~0); absorption check
  uses tiny minute deltas (5-255 shares observed).
- Fix: two adder sets — minuteBuy/minuteSell (reset per flush) and
  dayBuy/daySell (reset at EOD). Use dayDelta for absorption / delta%.

### M2. Absorption rule too loose
- Current: delta > 0 AND price < VWAP - 0.1% — that is a trend condition.
- Roadmap: positive delta while price flat/down over a recent window.
- Fix: cumDelta rising over last 10 min AND price change <= 0 over same window
  (RollingWindow already exists).

### M3. Calibration leakage in Level 3
- CalibratedClassifierCV(cv='prefit') fit on the test set, then evaluated on
  the same set → optimistic AUC.
- Fix: 3-way split train/calibration/test by date, or purged K-fold.

### M4. Class imbalance not handled
- Positive rate 14.9%, no scale_pos_weight → model under-predicts positives
  (max prob 0.34). Fix: scale_pos_weight = neg/pos ~ 5.7; track PR-AUC.

### M5. Partial recalculation on hostile regime
- Hostile-regime branch updates rawScore/score but not
  bullish/bearish/confidence → DTO fields disagree. Fix: derive all outputs
  once at a single exit point.

### M6. Level 2 bucket boundaries overlap
- BETWEEN 1 AND 2 and BETWEEN 2 AND 3 both include 2.0. Use half-open ranges
  (>= 1 AND < 2).

### M7. impact_coeff = 0.0 becomes NULL
- float(x) if x else None drops legitimate zeros. Use "is not None".

---

## MINOR / HYGIENE

- Kite token expires daily — add 08:45 IST health check alerting when
  auth.isAuthenticated() is false, otherwise the whole day silently degrades.
- After M1, label dashboard column "Δ 1m" to avoid ambiguity.
- delta_prediction_history stores inputs but nothing measures outcomes.
  Add an EOD job computing daily directional hit-rate, calibration, profit
  factor, false-breakout rate (roadmap "How to know the model is good").
- Regime gap_pct uses equal-weighted average of all symbols — switch to the
  actual NIFTY 50 index quote from Kite.
- VIX percentile unstable with <10 days history; keep NORMAL until enough data.

---

## PRIORITY ORDER (best result, least effort first)

| # | Fix | Impact | Effort |
|---|-----|--------|--------|
| 1 | C1 paper-trade thresholds | Unblocks paper trading | DONE |
| 2 | C2 base-rate normalization of ML blend | Removes systematic bearish bias in every score | Small |
| 3 | C3 impact_coeff on delta_strength | expectedMove direction becomes meaningful | Small (SQL) |
| 4 | C5 add delta features to ML | Model learns actual order-flow signal | Small |
| 5 | M4 class weighting | Usable probability range | Tiny |
| 6 | C4 conditional bucket probabilities | histProb becomes a real edge | Medium |
| 7 | M1 minute vs day delta split | Correct absorption + delta% + display | Medium |
| 8 | C6 live/daily ML scoring | Predictions reflect current state | Medium-Large |
| 9 | M3 calibration split | Honest metrics | Small |
| 10 | Accuracy-tracking EOD job | Closes the feedback loop | Medium |

Rule of thumb: apply fixes 2-5, retrain (--level all --days 60), then
paper-trade one full week before changing anything else. One change at a time
so cause and effect stay visible.
