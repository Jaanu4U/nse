package com.nse.ingest.service;

import com.nse.ingest.engine.DeltaEngine;
import com.nse.ingest.engine.IndicatorEngine;
import com.nse.ingest.engine.LeeReadyClassifier;
import com.nse.ingest.engine.RollingWindow;

import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.LongAdder;
import java.util.concurrent.locks.StampedLock;

/**
 * All in-memory state for one NSE symbol.
 * Each field group uses the lowest-overhead synchronisation available:
 *   StampedLock for floating-point OHLC / VWAP (brief write, optimistic read)
 *   LongAdder  for volume / trade counters (fully lock-free)
 *   DeltaEngine / IndicatorEngine have their own internal locks
 */
public class SymbolState {

    private final String symbol;

    // --- Price state ---
    private final StampedLock lock = new StampedLock();
    private double ltp          = 0;
    private double open         = Double.NaN;
    private double high         = Double.NEGATIVE_INFINITY;
    private double low          = Double.POSITIVE_INFINITY;
    private double close        = 0;
    private double vwapNum      = 0.0;
    private double bestBidPrice = Double.NaN;
    private double bestAskPrice = Double.NaN;
    private long   bestBidQty   = 0;
    private long   bestAskQty   = 0;

    // --- Per-minute bar state (D1 FIX: reset by aggregator each flush) ---
    private double minuteOpen = Double.NaN;
    private double minuteHigh = Double.NEGATIVE_INFINITY;
    private double minuteLow  = Double.POSITIVE_INFINITY;
    private final LongAdder minuteVolumeAdder = new LongAdder();

    // --- D3 FIX: cumulative day volume from the last Kite snapshot tick.
    // True interval volume = tick.volume - prevCumVolume (captures ALL trades
    // between snapshots, not just the last one). -1 = no tick seen yet today.
    private volatile long prevCumVolume = -1;

    // RSI (Wilder 14-period)
    private double avgGain = 0, avgLoss = 0, prevRsiClose = Double.NaN;
    private int    rsiPeriod = 0;

    // --- Lock-free counters ---
    private final LongAdder totalVolumeAdder = new LongAdder();
    private final LongAdder tradeCountAdder  = new LongAdder();

    // --- Sub-engines ---
    private final DeltaEngine      deltaEngine = new DeltaEngine();
    private final IndicatorEngine  indicators  = new IndicatorEngine();

    // --- Lee-Ready state ---
    private volatile double                      prevTickPrice = Double.NaN;
    private volatile LeeReadyClassifier.Direction prevTickDir  = LeeReadyClassifier.Direction.BUY;
    private final    LeeReadyClassifier          classifier    = new LeeReadyClassifier();

    // --- Rolling windows ---
    public final RollingWindow window1m  = new RollingWindow(1);
    public final RollingWindow window5m  = new RollingWindow(5);
    public final RollingWindow window15m = new RollingWindow(15);
    public final RollingWindow windowDay = new RollingWindow(375);

    private volatile long avgVolume20d = 0;
    // Per-minute avg volume (key = IST minute-of-day 0-1439) from delta_minute_candle
    private volatile Map<Integer, Long> avgVolumeByMinute = null;

    // --- Level 2 / Level 3 profile (loaded from DB at startup) ---
    private volatile double historicalUpProb = 0.0;  // from delta_stock_profiles
    private volatile double mlUpProb         = 0.0;  // from delta_ml_predictions
    private volatile double mlConfidence     = 0.0;
    private volatile double impactCoeff      = 0.0;  // Future Move% / DeltaStrength%
    private volatile double avgMove15m       = 0.0;  // avg 15-min move from L2
    // C4: per-bucket conditional up_prob keyed on delta_strength bucket
    // Map: "weak" | "normal" | "strong" | "very_strong" -> conditional up_prob (0-100)
    private volatile Map<String, Double> bucketUpProbs = Map.of();
    // Calculated live each score cycle
    private volatile double expectedMove     = 0.0;  // deltaStrength * impactCoeff * multiplier
    private volatile double expectedTarget   = 0.0;  // ltp * (1 + expectedMove/100)
    private volatile boolean absorptionFlag  = false; // +delta but price flat/down

    public SymbolState(String symbol) {
        this.symbol = symbol;
    }

    // ---- Tick ingestion -------------------------------------------------

    public void acceptTick(double price, long lastQty, long cumVolume,
                           double bidPrice, double bidQty,
                           double askPrice, double askQty) {

        // D3 FIX: derive the TRUE traded quantity for this snapshot interval
        // from the cumulative day volume delta. Kite ticks are ~1/sec snapshots;
        // lastTradedQty only reports the final trade and misses everything else.
        long tradedQty;
        long prev = prevCumVolume;
        if (cumVolume > 0) {
            if (prev < 0) {
                // First tick of the day: don't attribute the whole opening
                // cumulative volume to one direction — start with lastQty.
                tradedQty = lastQty;
            } else if (cumVolume >= prev) {
                tradedQty = cumVolume - prev;
            } else {
                tradedQty = 0; // out-of-order snapshot, ignore
            }
            prevCumVolume = cumVolume;
        } else {
            tradedQty = lastQty; // packet without volume (LTP mode fallback)
        }

        LeeReadyClassifier.Direction dir = classifier.classify(
                price, bidPrice, askPrice, prevTickPrice, prevTickDir);
        prevTickPrice = price;
        prevTickDir   = dir;

        if (tradedQty > 0) {
            deltaEngine.record(tradedQty, dir);
            totalVolumeAdder.add(tradedQty);
            minuteVolumeAdder.add(tradedQty);
            tradeCountAdder.increment();
        }

        long stamp = lock.writeLock();
        try {
            if (Double.isNaN(open)) open = price;
            if (price > high) high = price;
            if (price < low)  low  = price;
            // D1 FIX: per-minute bar tracking
            if (tradedQty > 0) {
                if (Double.isNaN(minuteOpen)) minuteOpen = price;
                if (price > minuteHigh) minuteHigh = price;
                if (price < minuteLow)  minuteLow  = price;
            }
            ltp   = price;
            close = price;
            vwapNum += price * tradedQty;
            bestBidPrice = bidPrice;
            bestBidQty   = (long) bidQty;
            bestAskPrice = askPrice;
            bestAskQty   = (long) askQty;
            updateRsi(price);
        } finally {
            lock.unlockWrite(stamp);
        }

        // Update indicators (has its own StampedLock internally)
        indicators.update(
            Double.isNaN(high) ? price : high,
            Double.isNaN(low)  ? price : low,
            price
        );
    }

    // ---- RSI (Wilder's 14-period) ----------------------------------------
    private void updateRsi(double price) {
        if (Double.isNaN(prevRsiClose)) { prevRsiClose = price; return; }
        double change = price - prevRsiClose;
        prevRsiClose = price;
        double gain = Math.max(change, 0), loss = Math.max(-change, 0);
        if (rsiPeriod < 14) {
            avgGain = (avgGain * rsiPeriod + gain) / (rsiPeriod + 1);
            avgLoss = (avgLoss * rsiPeriod + loss) / (rsiPeriod + 1);
            rsiPeriod++;
        } else {
            avgGain = (avgGain * 13 + gain) / 14.0;
            avgLoss = (avgLoss * 13 + loss) / 14.0;
        }
    }

    // ---- Read accessors --------------------------------------------------
    public double getLtp()   { return readD(() -> ltp); }
    public double getOpen()  { return readD(() -> open); }
    public double getHigh()  { return readD(() -> high); }
    public double getLow()   { return readD(() -> low); }
    public double getClose() { return readD(() -> close); }
    public double getVwap()  {
        long s = lock.tryOptimisticRead();
        double num = vwapNum;
        if (!lock.validate(s)) { s = lock.readLock(); try { num = vwapNum; } finally { lock.unlockRead(s); } }
        long vol = totalVolumeAdder.sum();
        return vol == 0 ? 0.0 : num / vol;
    }
    public double getIntradayHigh()          { return getHigh(); }
    public double getIntradayLow()           { return getLow(); }
    public double getOrderBookImbalance()    { long bid = bestBidQty, ask = bestAskQty; return (bid + ask) == 0 ? 0.0 : (double)(bid - ask) / (bid + ask); }
    public double getRsi() {
        if (avgLoss == 0) return 100.0;
        return 100.0 - (100.0 / (1.0 + avgGain / avgLoss));
    }
    public long   getTotalVolume()    { return totalVolumeAdder.sum(); }
    public int    getTradeCount()     { return (int) tradeCountAdder.sum(); }
    public long   getBestBidQty()     { return bestBidQty; }
    public long   getBestAskQty()     { return bestAskQty; }
    public long   getAvgVolume20d()   { return avgVolume20d; }
    public void   setAvgVolume20d(long v) { this.avgVolume20d = v; }
    public void   setAvgVolumeByMinute(Map<Integer, Long> m) { this.avgVolumeByMinute = m; }

    /**
     * Returns the expected cumulative volume up to the current IST minute,
     * by summing per-minute historical averages from 09:15 to now.
     * Falls back to daily avg when no minute data available.
     */
    private long getAvgVolumeSameTime() {
        Map<Integer, Long> m = avgVolumeByMinute;
        if (m != null && !m.isEmpty()) {
            int marketOpen = 9 * 60 + 15; // 09:15 IST = minute 555
            int currentMin = ZonedDateTime.now(ZoneId.of("Asia/Kolkata"))
                .toLocalTime().toSecondOfDay() / 60;
            long cumulative = 0;
            for (int min = marketOpen; min <= currentMin; min++) {
                Long v = m.get(min);
                if (v != null) cumulative += v;
            }
            if (cumulative > 0) return cumulative;
        }
        return avgVolume20d > 0 ? avgVolume20d : totalVolumeAdder.sum();
    }

    /**
     * Delta Strength % = Current-minute Net Delta / 20-day Avg Same-Time-Minute Volume × 100
     *
     * Uses deltaRate (last 1-min net delta) vs per-minute historical avg — true same-time comparison.
     * Falls back to (totalDelta / totalVolume × 100) when no historical data available.
     */
    public double getDeltaStrength() {
        Map<Integer, Long> m = avgVolumeByMinute;
        if (m != null && !m.isEmpty()) {
            // Use last-minute net delta vs same-minute historical avg
            long minuteDelta = deltaEngine.getDeltaRate(); // net delta last ~1 min
            int minuteOfDay = ZonedDateTime.now(ZoneId.of("Asia/Kolkata"))
                .toLocalTime().toSecondOfDay() / 60;
            Long avgMinVol = m.get(minuteOfDay);
            if (avgMinVol != null && avgMinVol > 0) {
                return (double) minuteDelta / avgMinVol * 100.0;
            }
        }
        // Fallback: use cumulative delta vs total volume (raw delta%)
        long totalVol = totalVolumeAdder.sum();
        if (totalVol == 0) return 0.0;
        return (double) deltaEngine.getDelta() / totalVol * 100.0;
    }

    /** Volume Ratio = current minute volume pace vs 20-day avg same-time volume (1.0 = average) */
    public double getVolumeRatio() {
        long denom = getAvgVolumeSameTime();
        if (denom <= 0) return 1.0;
        // Compare today's total accumulated volume vs expected by this time of day
        long totalVol = totalVolumeAdder.sum();
        return totalVol > 0 ? (double) totalVol / denom : 1.0;
    }

    /** Delta % = cumulative day delta / total volume × 100 (raw imbalance quality)
     * M1 FIX: use getCumulativeDelta() (full-day) not getDelta() (current-minute only, resets each flush)
     */
    public double getDeltaPercent() {
        long vol = totalVolumeAdder.sum();
        if (vol == 0) return 0.0;
        return (double) deltaEngine.getCumulativeDelta() / vol * 100.0;
    }
    public String getSymbol()         { return symbol; }
    public DeltaEngine     getDeltaEngine() { return deltaEngine; }
    public IndicatorEngine getIndicators()  { return indicators; }

    // ---- Per-minute bar (D1/D2 FIX) ---------------------------------------

    /** Immutable snapshot of the just-completed 1-minute bar. */
    public record MinuteBar(double open, double high, double low, double close, long volume) {}

    /**
     * Called by the aggregator once per minute: returns the completed minute
     * bar (true per-minute OHLC + per-minute volume) and resets the bar state.
     * volume == 0 means no trades occurred this minute.
     */
    public MinuteBar snapshotAndResetMinuteBar() {
        long vol = minuteVolumeAdder.sumThenReset();
        long stamp = lock.writeLock();
        try {
            MinuteBar bar = new MinuteBar(minuteOpen, minuteHigh, minuteLow, ltp, vol);
            minuteOpen = Double.NaN;
            minuteHigh = Double.NEGATIVE_INFINITY;
            minuteLow  = Double.POSITIVE_INFINITY;
            return bar;
        } finally {
            lock.unlockWrite(stamp);
        }
    }

    // Level 2 / 3 profile accessors
    public double  getHistoricalUpProb() { return historicalUpProb; }
    public double  getMlUpProb()         { return mlUpProb; }
    public double  getMlConfidence()     { return mlConfidence; }
    public double  getImpactCoeff()      { return impactCoeff; }
    public double  getAvgMove15m()       { return avgMove15m; }
    public double  getExpectedMove()     { return expectedMove; }
    public double  getExpectedTarget()   { return expectedTarget; }
    public boolean isAbsorption()        { return absorptionFlag; }

    /**
     * C4: Return the conditional up_prob for the live delta_strength bucket.
     * Falls back to up_prob_overall (historicalUpProb) when no bucket data.
     * Buckets: weak (<|2%|), normal ([2,5)), strong ([5,10)), very_strong (>=10).
     */
    public double getBucketUpProb(double deltaStrength) {
        Map<String, Double> buckets = bucketUpProbs;
        if (buckets.isEmpty()) return historicalUpProb;
        String key;
        double abs = Math.abs(deltaStrength);
        if      (abs >= 10.0) key = "very_strong";
        else if (abs >=  5.0) key = "strong";
        else if (abs >=  2.0) key = "normal";
        else                  key = "weak";
        Double v = buckets.get(key);
        return (v != null) ? v : historicalUpProb;
    }

    public void setHistoricalUpProb(double v) { this.historicalUpProb = v; }
    public void setMlUpProb(double v)         { this.mlUpProb = v; }
    public void setMlConfidence(double v)     { this.mlConfidence = v; }
    public void setImpactCoeff(double v)      { this.impactCoeff = v; }
    public void setAvgMove15m(double v)       { this.avgMove15m = v; }
    public void setBucketUpProbs(Map<String, Double> m) { this.bucketUpProbs = m; }
    public void setExpectedMove(double v)     { this.expectedMove = v; }
    public void setExpectedTarget(double v)   { this.expectedTarget = v; }
    public void setAbsorptionFlag(boolean v)  { this.absorptionFlag = v; }

    /** Called at 15:35 — clear all intraday state. */
    public void reset() {
        long stamp = lock.writeLock();
        try {
            open = Double.NaN; high = Double.NEGATIVE_INFINITY; low = Double.POSITIVE_INFINITY;
            close = 0; ltp = 0; vwapNum = 0;
            minuteOpen = Double.NaN; minuteHigh = Double.NEGATIVE_INFINITY; minuteLow = Double.POSITIVE_INFINITY;
            bestBidPrice = Double.NaN; bestAskPrice = Double.NaN;
            bestBidQty = 0; bestAskQty = 0;
            rsiPeriod = 0; avgGain = 0; avgLoss = 0; prevRsiClose = Double.NaN;
        } finally { lock.unlockWrite(stamp); }
        totalVolumeAdder.reset();
        minuteVolumeAdder.reset();
        tradeCountAdder.reset();
        deltaEngine.reset();
        prevCumVolume = -1;
        prevTickPrice = Double.NaN;
        prevTickDir   = LeeReadyClassifier.Direction.BUY;
        // Note: IndicatorEngine keeps historical EMA state across days (intentional)
    }

    private double readD(java.util.function.DoubleSupplier fn) {
        long s = lock.tryOptimisticRead();
        double v = fn.getAsDouble();
        if (!lock.validate(s)) {
            s = lock.readLock(); try { v = fn.getAsDouble(); } finally { lock.unlockRead(s); }
        }
        return v;
    }
}
