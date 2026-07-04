package com.nse.ingest.engine;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class DeltaEngineTest {

    private DeltaEngine engine;

    @BeforeEach
    void setUp() { engine = new DeltaEngine(); }

    @Test
    void buyIncreasesVolume() {
        engine.record(100, LeeReadyClassifier.Direction.BUY);
        assertEquals(100, engine.getBuyVolume());
        assertEquals(0,   engine.getSellVolume());
        assertEquals(100, engine.getDelta());
        assertEquals(100, engine.getCumulativeDelta());
    }

    @Test
    void sellDecreasesVolume() {
        engine.record(50, LeeReadyClassifier.Direction.SELL);
        assertEquals(0,    engine.getBuyVolume());
        assertEquals(50,   engine.getSellVolume());
        assertEquals(-50,  engine.getDelta());
        assertEquals(-50,  engine.getCumulativeDelta());
    }

    @Test
    void mixedDelta() {
        engine.record(200, LeeReadyClassifier.Direction.BUY);
        engine.record(80,  LeeReadyClassifier.Direction.SELL);
        assertEquals(200, engine.getBuyVolume());
        assertEquals(80,  engine.getSellVolume());
        assertEquals(120, engine.getDelta());
        assertEquals(120, engine.getCumulativeDelta());
    }

    @Test
    void resetClearsAll() {
        engine.record(100, LeeReadyClassifier.Direction.BUY);
        engine.reset();
        assertEquals(0, engine.getBuyVolume());
        assertEquals(0, engine.getCumulativeDelta());
    }
}
