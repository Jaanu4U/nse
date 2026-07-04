package com.nse.ingest.engine;

import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.LongAdder;

/**
 * Maintains running buy/sell volumes, delta, cumulative delta and
 * delta rate for a single symbol.  All updates are lock-free.
 */
public class DeltaEngine {

    private final LongAdder buyVolume       = new LongAdder();
    private final LongAdder sellVolume      = new LongAdder();
    private final AtomicLong cumulativeDelta = new AtomicLong(0);
    // Rate: delta accumulated in the last 60 seconds (approximate)
    private volatile long lastMinuteDelta   = 0;
    private volatile long lastResetEpoch    = System.currentTimeMillis();

    public void record(long qty, LeeReadyClassifier.Direction dir) {
        if (dir == LeeReadyClassifier.Direction.BUY) {
            buyVolume.add(qty);
            cumulativeDelta.addAndGet(qty);
        } else {
            sellVolume.add(qty);
            cumulativeDelta.addAndGet(-qty);
        }
        maybeResetRate();
    }

    /** Called every minute by the aggregator to capture and reset the rate. */
    public long snapshotAndResetRate() {
        long buy = buyVolume.sumThenReset();
        long sell = sellVolume.sumThenReset();
        lastMinuteDelta = buy - sell;
        lastResetEpoch  = System.currentTimeMillis();
        return lastMinuteDelta;
    }

    private void maybeResetRate() {
        long now = System.currentTimeMillis();
        if (now - lastResetEpoch > 60_000) {
            lastResetEpoch = now;
        }
    }

    public long getBuyVolume()        { return buyVolume.sum(); }
    public long getSellVolume()       { return sellVolume.sum(); }
    public long getDelta()            { return getBuyVolume() - getSellVolume(); }
    public long getCumulativeDelta()  { return cumulativeDelta.get(); }
    public long getDeltaRate()        { return lastMinuteDelta; }

    public void reset() {
        buyVolume.reset();
        sellVolume.reset();
        cumulativeDelta.set(0);
        lastMinuteDelta = 0;
    }
}
