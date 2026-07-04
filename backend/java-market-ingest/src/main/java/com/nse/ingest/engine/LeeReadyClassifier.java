package com.nse.ingest.engine;

/**
 * Lee-Ready tick-direction classification.
 *
 * Priority:
 * 1. If trade price >= best ask  → BUY  (aggressive buy)
 * 2. If trade price <= best bid  → SELL (aggressive sell)
 * 3. If midpoint available and price > midpoint → BUY
 * 4. If midpoint available and price < midpoint → SELL
 * 5. Tick-rule fallback: compare to previous trade price
 *    - uptick   → BUY
 *    - downtick → SELL
 *    - zero-tick: inherit previous direction
 */
public class LeeReadyClassifier {

    /** Immutable result object. */
    public enum Direction { BUY, SELL }

    /**
     * Classify a single tick.
     *
     * @param price     current trade price
     * @param bestBid   best bid price (NaN if not available)
     * @param bestAsk   best ask price (NaN if not available)
     * @param prevPrice previous trade price (NaN on first tick)
     * @param prevDir   previous classified direction (BUY if unknown)
     */
    public Direction classify(double price,
                               double bestBid,
                               double bestAsk,
                               double prevPrice,
                               Direction prevDir) {
        // Rule 1 & 2 – quote test
        if (!Double.isNaN(bestAsk) && price >= bestAsk) return Direction.BUY;
        if (!Double.isNaN(bestBid) && price <= bestBid) return Direction.SELL;

        // Rule 3 & 4 – midpoint test (Lee-Ready proper)
        if (!Double.isNaN(bestBid) && !Double.isNaN(bestAsk)) {
            double mid = (bestBid + bestAsk) / 2.0;
            if (price > mid) return Direction.BUY;
            if (price < mid) return Direction.SELL;
        }

        // Rule 5 – tick test fallback
        if (!Double.isNaN(prevPrice)) {
            if (price > prevPrice) return Direction.BUY;
            if (price < prevPrice) return Direction.SELL;
        }

        // Zero-tick: inherit
        return prevDir != null ? prevDir : Direction.BUY;
    }
}
