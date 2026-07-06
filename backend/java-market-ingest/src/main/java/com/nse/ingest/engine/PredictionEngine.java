package com.nse.ingest.engine;

import com.nse.ingest.dto.PredictionResultDto;
import com.nse.ingest.service.SymbolState;
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
