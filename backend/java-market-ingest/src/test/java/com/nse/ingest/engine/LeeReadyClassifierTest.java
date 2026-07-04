package com.nse.ingest.engine;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class LeeReadyClassifierTest {

    private final LeeReadyClassifier clf = new LeeReadyClassifier();

    @Test
    void buyWhenAtOrAboveAsk() {
        assertEquals(LeeReadyClassifier.Direction.BUY,
            clf.classify(100.5, 100.0, 100.4, Double.NaN, null));
    }

    @Test
    void sellWhenAtOrBelowBid() {
        assertEquals(LeeReadyClassifier.Direction.SELL,
            clf.classify(99.9, 100.0, 100.5, Double.NaN, null));
    }

    @Test
    void buyWhenAboveMidpoint() {
        // bid=100, ask=101, mid=100.5, price=100.7 → BUY
        assertEquals(LeeReadyClassifier.Direction.BUY,
            clf.classify(100.7, 100.0, 101.0, Double.NaN, null));
    }

    @Test
    void sellWhenBelowMidpoint() {
        // bid=100, ask=101, mid=100.5, price=100.3 → SELL
        assertEquals(LeeReadyClassifier.Direction.SELL,
            clf.classify(100.3, 100.0, 101.0, Double.NaN, null));
    }

    @Test
    void uptickFallback() {
        assertEquals(LeeReadyClassifier.Direction.BUY,
            clf.classify(101.0, Double.NaN, Double.NaN, 100.0, null));
    }

    @Test
    void downtickFallback() {
        assertEquals(LeeReadyClassifier.Direction.SELL,
            clf.classify(99.0, Double.NaN, Double.NaN, 100.0, null));
    }

    @Test
    void zeroTickInherits() {
        assertEquals(LeeReadyClassifier.Direction.SELL,
            clf.classify(100.0, Double.NaN, Double.NaN, 100.0, LeeReadyClassifier.Direction.SELL));
    }
}
