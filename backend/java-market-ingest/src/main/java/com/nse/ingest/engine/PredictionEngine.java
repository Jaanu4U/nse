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

    // Signal weights (must sum to 1.0)
    private static final double W_DELTA          = 0.20;
    private static final double W_CUM_DELTA      = 0.15;
    private static final double W_VWAP           = 0.12;
    private static final double W_VOLUME         = 0.08;
    private static final double W_OBI            = 0.15;  // order-book imbalance
    private static final double W_RSI            = 0.10;
    private static final double W_PRICE_POS      = 0.10;  // price vs intraday range
    private static final double W_VOLUME_SPIKE   = 0.05;
    private static final double W_DELTA_MOMENTUM = 0.05;

    public PredictionResultDto score(SymbolState state) {
        double rawScore = 0.0;

        // 1. Delta signal: (buyVol - sellVol) / totalVol  → [-1, +1]
        long totalVol = state.getTotalVolume();
        if (totalVol > 0) {
            double deltaRatio = (double) state.getDeltaEngine().getDelta() / totalVol;
            rawScore += W_DELTA * clamp(deltaRatio, -1, 1);
        }

        // 2. Cumulative delta trend: sign and magnitude
        long cumDelta = state.getDeltaEngine().getCumulativeDelta();
        double cumDeltaSignal = totalVol > 0 ? clamp((double) cumDelta / totalVol, -1, 1) : 0;
        rawScore += W_CUM_DELTA * cumDeltaSignal;

        // 3. VWAP deviation: (ltp - vwap) / vwap → buy above vwap
        double vwap = state.getVwap();
        double ltp  = state.getLtp();
        if (vwap > 0) {
            double vwapDev = clamp((ltp - vwap) / vwap * 50, -1, 1);
            rawScore += W_VWAP * vwapDev;
        }

        // 4. Volume spike: volume vs 20-day avg (approximated by today's pace)
        double volSpike = 0;
        long avgVol20 = state.getAvgVolume20d();
        if (avgVol20 > 0) {
            double pace = (double) totalVol / avgVol20;
            volSpike = clamp(pace - 1.0, -1, 1);
        }
        rawScore += W_VOLUME * volSpike;
        rawScore += W_VOLUME_SPIKE * volSpike;

        // 5. Order-book imbalance: (bidVol - askVol) / (bidVol + askVol) → [-1,+1]
        long bidVol = state.getBestBidQty();
        long askVol = state.getBestAskQty();
        double obi = (bidVol + askVol) > 0
                ? (double)(bidVol - askVol) / (bidVol + askVol)
                : 0;
        rawScore += W_OBI * obi;

        // 6. RSI: centre on 50, scale: rsi>60 bullish, <40 bearish
        double rsi = state.getRsi();
        double rsiSignal = clamp((rsi - 50.0) / 50.0, -1, 1);
        rawScore += W_RSI * rsiSignal;

        // 7. Price position in intraday range
        double high = state.getIntradayHigh();
        double low  = state.getIntradayLow();
        double pricePos = (high > low) ? clamp((ltp - low) / (high - low) * 2 - 1, -1, 1) : 0;
        rawScore += W_PRICE_POS * pricePos;

        // 8. Delta momentum: delta rate vs previous rate
        long deltaRate = state.getDeltaEngine().getDeltaRate();
        double dmSignal = totalVol > 0 ? clamp((double) deltaRate / (totalVol + 1) * 10, -1, 1) : 0;
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
