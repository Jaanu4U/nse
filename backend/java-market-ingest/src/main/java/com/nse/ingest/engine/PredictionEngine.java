package com.nse.ingest.engine;

import com.nse.ingest.dto.PredictionResultDto;
import com.nse.ingest.service.SymbolState;
import com.nse.ingest.scheduler.MarketScheduler;
import org.springframework.stereotype.Component;

/**
 * Generates a prediction score (0-100) and bullish/bearish probabilities
 * from a set of in-memory technical and order-flow signals.
 *
 * Scoring model – additive weighted z-score approach:
 * Each signal is normalised to [-1, +1] and weighted.  The final score is
 * mapped from [-1,+1] to [0,100].
 */
@Component
public class PredictionEngine {

    // Level-1 signal weights — matches roadmap formula:
    // 25% Delta Strength + 15% Cum-Delta + 15% VWAP + 15% Volume + 10% OBI + 10% Trend + 10% Momentum
    private static final double W_DELTA_STRENGTH = 0.25;  // normalized delta (core signal)
    private static final double W_CUM_DELTA      = 0.15;  // cumulative delta direction
    private static final double W_VWAP           = 0.15;  // price vs VWAP
    private static final double W_VOLUME         = 0.15;  // volume expansion
    private static final double W_OBI            = 0.10;  // order-book imbalance
    private static final double W_TREND          = 0.10;  // RSI + EMA trend
    private static final double W_DELTA_MOMENTUM = 0.10;  // delta rate momentum

    public PredictionResultDto score(SymbolState state) {
        double rawScore = 0.0;
        long totalVol = state.getTotalVolume();

        // 1. Delta Strength (25%) — normalized delta vs 20-day same-time avg volume
        //    deltaStrength% = netDelta / avgSameTimeVol20d × 100  → cap at ±20% for scoring
        double deltaStrength = state.getDeltaStrength();
        rawScore += W_DELTA_STRENGTH * clamp(deltaStrength / 20.0, -1, 1);

        // 2. Cumulative Delta Direction (15%) — is buying pressure building?
        long cumDelta = state.getDeltaEngine().getCumulativeDelta();
        double cumDeltaSignal = totalVol > 0 ? clamp((double) cumDelta / (totalVol + 1), -1, 1) : 0;
        rawScore += W_CUM_DELTA * cumDeltaSignal;

        // 3. VWAP Position (15%) — institutional bias: above = bullish, below = bearish
        //    Use tight scale: ±0.5% deviation from VWAP = full signal
        double vwap = state.getVwap();
        double ltp  = state.getLtp();
        if (vwap > 0) {
            double vwapDev = clamp((ltp - vwap) / vwap * 200, -1, 1);
            rawScore += W_VWAP * vwapDev;
        }

        // 4. Volume Expansion (15%) — confirms participation
        //    volumeRatio = currentVol / avgVol20d; >1.5x = strong, <0.5x = weak
        double volRatio = state.getVolumeRatio();
        double volSignal = clamp((volRatio - 1.0) * 0.8, -1, 1);
        // Adjust by delta direction (volume spike in direction of delta is more meaningful)
        if (deltaStrength < 0) volSignal = -Math.abs(volSignal);
        rawScore += W_VOLUME * volSignal;

        // 5. Order Book Imbalance (10%) — near-term liquidity pressure
        long bidVol = state.getBestBidQty();
        long askVol = state.getBestAskQty();
        double obi = (bidVol + askVol) > 0
                ? clamp((double)(bidVol - askVol) / (bidVol + askVol), -1, 1)
                : 0;
        rawScore += W_OBI * obi;

        // 6. Trend (10%) — RSI + EMA alignment
        //    RSI>60 and price>EMA20 = bullish trend confirmation
        double rsi = state.getRsi();
        double rsiSignal = clamp((rsi - 50.0) / 30.0, -1, 1);  // ±30 RSI points = full signal
        IndicatorEngine ind = state.getIndicators();
        double emaTrend = (vwap > 0 && ind.getEma20() > 0)
                ? clamp((ltp - ind.getEma20()) / (ind.getEma20() + 0.001) * 100, -1, 1)
                : 0;
        double trendSignal = (rsiSignal + emaTrend) / 2.0;
        // SuperTrend confirmation: +1 dir = bullish, -1 = bearish
        if (ind.getSuperTrendDir() != 0) trendSignal = (trendSignal + ind.getSuperTrendDir()) / 2.0;
        rawScore += W_TREND * trendSignal;

        // 7. Delta Momentum (10%) — is delta accelerating? (rising = stronger signal)
        long deltaRate = state.getDeltaEngine().getDeltaRate();
        double dmSignal = totalVol > 0 ? clamp((double) deltaRate / (totalVol + 1) * 20, -1, 1) : 0;
        rawScore += W_DELTA_MOMENTUM * dmSignal;

        // Map rawScore [-1,+1] → predictionScore [0,100]
        double score = (rawScore + 1.0) / 2.0 * 100.0;
        score = Math.max(0, Math.min(100, score));

        // Derive bullish/bearish probabilities via softmax-like normalisation
        double bullishLogit = rawScore;
        double bearishLogit = -rawScore;
        double expB = Math.exp(bullishLogit);
        double expS = Math.exp(bearishLogit);
        double bullish = expB / (expB + expS);
        double bearish = expS / (expB + expS);

        // Confidence: higher when score is far from 50
        double confidence = Math.abs(rawScore);

        // Blend L2 historical prob + L3 ML prob into final score
        // C2 FIX: normalize against actual distribution baselines, not 0.5
        //   histProb universe mean ≈ 46.0 (market drift); std-dev proxy = 8.0
        //   mlProb base rate ≈ 0.149 (14.9% of 15-min windows show +0.3% move)
        // C4 FIX: use conditional bucket up_prob (given live deltaStrength) instead of unconditional overall
        double histProb = state.getBucketUpProb(deltaStrength); // bucket-conditional; falls back to overall
        double mlProb   = state.getMlUpProb();                   // 0-1 range from DB
        if (histProb > 0 || mlProb > 0) {
            double histSignal = histProb > 0
                ? clamp((histProb - 46.0) / 8.0, -1, 1)        // +1 when histProb=54%, -1 when 38%
                : 0;
            double mlSignal = mlProb > 0
                ? clamp((mlProb - 0.149) / 0.149, -1.5, 1.5) / 1.5  // +1 when 2× base rate (0.30)
                : 0;
            double blended = (histProb > 0 && mlProb > 0)
                ? 0.40 * histSignal + 0.60 * mlSignal
                : (histProb > 0 ? histSignal : mlSignal);
            rawScore = rawScore * 0.70 + clamp(blended, -1, 1) * 0.30;
        }

        // Expected Move % = deltaStrength * impactCoeff * confirmationMultiplier
        // Only calculate when deltaStrength is meaningful (≥ 0.5% = active participation)
        double impactCoeff = state.getImpactCoeff();
        double expectedMove = 0.0;
        double deltaStrForMove = state.getDeltaStrength();
        if (impactCoeff != 0.0 && Math.abs(deltaStrForMove) >= 0.5) {
            double deltaStr = deltaStrForMove;
            // Confirmation multiplier: above VWAP & volume spike boost, below VWAP reduces
            double vwapMult = (state.getLtp() > state.getVwap() && state.getVwap() > 0) ? 1.15 : 0.75;
            double volMult  = state.getVolumeRatio() > 2.0 ? 1.30 : state.getVolumeRatio() > 1.5 ? 1.15 : 1.0;
            double confirmMult = vwapMult * volMult;
            expectedMove = deltaStr * impactCoeff * confirmMult;
            expectedMove = Math.max(-5.0, Math.min(5.0, expectedMove)); // cap at ±5%
        } else if (state.getAvgMove15m() != 0.0) {
            // Fallback: use historical avg move scaled by current volume ratio
            expectedMove = state.getAvgMove15m() * Math.min(state.getVolumeRatio(), 2.0);
        }
        double expectedTarget = state.getLtp() > 0 ? state.getLtp() * (1 + expectedMove / 100.0) : 0.0;

        // Apply regime confirmation multiplier (VIX level, expiry day, gap day)
        var regime = MarketScheduler.REGIME;
        double regimeMult = regime.getConfirmationMultiplier();
        if (regimeMult != 1.0) {
            expectedMove *= regimeMult;
            expectedTarget = state.getLtp() > 0 ? state.getLtp() * (1 + expectedMove / 100.0) : 0.0;
        }
        // On hostile regime (high VIX + expiry/gap), reduce blended score confidence
        if (regime.isHostileRegime()) {
            rawScore *= 0.80;
        }

        // M5 FIX: single exit point — derive ALL output fields from final rawScore
        score      = Math.max(0, Math.min(100, (rawScore + 1.0) / 2.0 * 100.0));
        double expBf = Math.exp(rawScore);
        double expSf = Math.exp(-rawScore);
        bullish    = expBf / (expBf + expSf);
        bearish    = expSf / (expBf + expSf);
        confidence = Math.abs(rawScore);

        // M2 FIX: Absorption detection — buying pressure (cumDelta rising) but price flat/down.
        // Use 15-min rolling window so the check is stable after each minute flush.
        // Criteria: net buying over last 15 min (totalDelta > 0) AND
        //           price change over that window is <= 0 (price not responding to buying).
        boolean absorption = false;
        if (state.window15m.getFilled() >= 3) {
            long win15Delta = state.window15m.totalDelta();
            double win15Low  = state.window15m.windowLow();
            double win15High = state.window15m.windowHigh();
            // Window open approximation: low when going up (first bar low), but simpler:
            // if totalDelta is strongly positive and current price is at/below window low + small band
            if (win15Delta > 0) {
                // Price flat/down: current ltp is no more than 0.1% above the window low
                double ltp15 = state.getLtp();
                if (win15High > 0 && ltp15 > 0) {
                    double priceRangeRatio = (ltp15 - win15Low) / (win15High - win15Low + 0.001);
                    // In the lower 30% of the 15-min range despite net buying = absorption
                    if (priceRangeRatio <= 0.30) absorption = true;
                }
            }
        }
        if (absorption) { expectedMove *= 0.5; expectedTarget = state.getLtp() > 0 ? state.getLtp() * (1 + expectedMove / 100.0) : 0.0; }

        // Store back into state for SSE stream
        state.setExpectedMove(Math.round(expectedMove * 10000) / 10000.0);
        state.setExpectedTarget(Math.round(expectedTarget * 100) / 100.0);
        state.setAbsorptionFlag(absorption);

        String signal = rawScore > 0.2 ? "BULLISH" : rawScore < -0.2 ? "BEARISH" : "NEUTRAL";

        return new PredictionResultDto(
            state.getSymbol(),
            Math.round(bullish * 10000) / 10000.0,
            Math.round(bearish * 10000) / 10000.0,
            Math.round(confidence * 10000) / 10000.0,
            Math.round(score * 100) / 100.0,
            signal
        );
    }

    private static double clamp(double v, double min, double max) {
        return Math.max(min, Math.min(max, v));
    }
}
