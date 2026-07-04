package com.nse.ingest.engine;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class RollingWindowTest {

    @Test
    void pushedDataReflectsInTotals() {
        RollingWindow w = new RollingWindow(5);
        w.push(1000, 100, 110, 95, 108, 500, 300, 200, 100);
        w.push(1060, 108, 115, 105, 112, 600, 350, 250, 100);
        assertEquals(1100, w.totalVolume());
        assertEquals(200,  w.totalDelta());
        assertEquals(115,  w.windowHigh(), 0.001);
        assertEquals(95,   w.windowLow(),  0.001);
    }

    @Test
    void ringBufferWrapsCorrectly() {
        RollingWindow w = new RollingWindow(2); // only keeps 2 bars
        w.push(1, 10, 12, 9, 11, 100, 60, 40, 20);
        w.push(2, 11, 13, 10, 12, 200, 120, 80, 40);
        w.push(3, 12, 14, 11, 13, 300, 180, 120, 60); // should evict first
        // Filled is capped at capacity-1 = 2
        assertTrue(w.getFilled() <= 2);
    }
}
