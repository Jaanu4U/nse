package com.nse.ingest.engine;

import java.util.concurrent.atomic.AtomicInteger;

/**
 * Lock-free ring-buffer for rolling OHLCV and delta statistics over a fixed
 * number of one-minute bars.  Designed for a single-writer / multiple-reader
 * access pattern: the tick processor writes, REST API threads read.
 *
 * windowMinutes = 1  → 1-min bar summary (last bar only)
 * windowMinutes = 5  → 5-min rolling window
 * windowMinutes = 15 → 15-min rolling window
 * windowMinutes = 375 → full day (6h15m of NSE trading)
 */
public class RollingWindow {

    private final int capacity;           // number of 1-min slots kept
    private final double[] opens;
    private final double[] highs;
    private final double[] lows;
    private final double[] closes;
    private final long[]   volumes;
    private final long[]   buyVols;
    private final long[]   sellVols;
    private final long[]   deltas;
    private final long[]   timestamps;   // unix epoch of bar minute
    private final AtomicInteger writeHead = new AtomicInteger(0);
    private volatile int filled = 0;

    public RollingWindow(int windowMinutes) {
        this.capacity = windowMinutes + 1; // one extra to avoid head==tail ambiguity
        opens      = new double[capacity];
        highs      = new double[capacity];
        lows       = new double[capacity];
        closes     = new double[capacity];
        volumes    = new long[capacity];
        buyVols    = new long[capacity];
        sellVols   = new long[capacity];
        deltas     = new long[capacity];
        timestamps = new long[capacity];
    }

    /** Called every minute by the aggregator to push a completed bar. */
    public void push(long ts, double open, double high, double low, double close,
                     long volume, long buyVol, long sellVol, long delta) {
        int idx = writeHead.getAndIncrement() % capacity;
        opens[idx]      = open;
        highs[idx]      = high;
        lows[idx]       = low;
        closes[idx]     = close;
        volumes[idx]    = volume;
        buyVols[idx]    = buyVol;
        sellVols[idx]   = sellVol;
        deltas[idx]     = delta;
        timestamps[idx] = ts;
        if (filled < capacity - 1) filled++;
    }

    /** Returns the sum of volume across all filled slots. */
    public long totalVolume() {
        long sum = 0;
        for (int i = 0; i < filled; i++) sum += volumes[i];
        return sum;
    }

    /** Returns the sum of delta across all filled slots. */
    public long totalDelta() {
        long sum = 0;
        for (int i = 0; i < filled; i++) sum += deltas[i];
        return sum;
    }

    /** Highest high in the window. */
    public double windowHigh() {
        double max = Double.NEGATIVE_INFINITY;
        for (int i = 0; i < filled; i++) if (highs[i] > max) max = highs[i];
        return max;
    }

    /** Lowest low in the window. */
    public double windowLow() {
        double min = Double.POSITIVE_INFINITY;
        for (int i = 0; i < filled; i++) if (lows[i] < min) min = lows[i];
        return min;
    }

    public int getFilled() { return filled; }
}
