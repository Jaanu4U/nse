package com.nse.ingest.service;

import com.nse.ingest.engine.DeltaEngine;
import com.nse.ingest.engine.IndicatorEngine;
import com.nse.ingest.engine.LeeReadyClassifier;
import com.nse.ingest.engine.RollingWindow;

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

    public SymbolState(String symbol) {
        this.symbol = symbol;
    }

    // ---- Tick ingestion -------------------------------------------------

    public void acceptTick(double price, long qty,
                           double bidPrice, double bidQty,
                           double askPrice, double askQty) {

        LeeReadyClassifier.Direction dir = classifier.classify(
                price, bidPrice, askPrice, prevTickPrice, prevTickDir);
        prevTickPrice = price;
        prevTickDir   = dir;

        deltaEngine.record(qty, dir);
        totalVolumeAdder.add(qty);
        tradeCountAdder.increment();

        long stamp = lock.writeLock();
        try {
            if (Double.isNaN(open)) open = price;
            if (price > high) high = price;
            if (price < low)  low  = price;
            ltp   = price;
            close = price;
            vwapNum += price * qty;
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
    public String getSymbol()         { return symbol; }
    public DeltaEngine     getDeltaEngine() { return deltaEngine; }
    public IndicatorEngine getIndicators()  { return indicators; }

    /** Called at 15:35 — clear all intraday state. */
    public void reset() {
        long stamp = lock.writeLock();
        try {
            open = Double.NaN; high = Double.NEGATIVE_INFINITY; low = Double.POSITIVE_INFINITY;
            close = 0; ltp = 0; vwapNum = 0;
            bestBidPrice = Double.NaN; bestAskPrice = Double.NaN;
            bestBidQty = 0; bestAskQty = 0;
            rsiPeriod = 0; avgGain = 0; avgLoss = 0; prevRsiClose = Double.NaN;
        } finally { lock.unlockWrite(stamp); }
        totalVolumeAdder.reset();
        tradeCountAdder.reset();
        deltaEngine.reset();
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
