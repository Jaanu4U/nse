package com.nse.ingest.engine;

import java.util.concurrent.locks.StampedLock;

/**
 * Real-time technical indicator calculator for a single symbol.
 * All indicators are updated on every tick — no DB queries ever.
 *
 * Indicators:
 *  EMA(20), EMA(50)                  — exponential moving averages
 *  ATR(14)   Wilder's average true range
 *  MACD      (EMA12 - EMA26) with Signal(9) and Histogram
 *  Bollinger Bands (20-period SMA ± 2σ)
 *  SuperTrend (ATR multiplier = 3)
 */
public class IndicatorEngine {

    private final StampedLock lock = new StampedLock();

    // EMA state
    private double ema20 = Double.NaN, ema50 = Double.NaN;
    private double ema12 = Double.NaN, ema26 = Double.NaN;
    private double macdSignal = Double.NaN;
    private int    count = 0;

    // ATR(14) — Wilder's smoothed
    private double prevClose = Double.NaN;
    private double atr14    = Double.NaN;
    private int    atrCount = 0;

    // Bollinger Bands(20)
    private static final int BB_PERIOD = 20;
    private final double[] bbWindow = new double[BB_PERIOD];
    private int bbHead = 0, bbFilled = 0;
    private double bbSMA = 0, bbStdDev = 0, bbUpper = 0, bbLower = 0;

    // SuperTrend state
    private static final double ST_MULT = 3.0;
    private double superTrend = Double.NaN;
    private int    stDir      = 0;     // +1 = bullish, -1 = bearish

    // EMA multipliers
    private static final double K20 = 2.0 / (20 + 1);
    private static final double K50 = 2.0 / (50 + 1);
    private static final double K12 = 2.0 / (12 + 1);
    private static final double K26 = 2.0 / (26 + 1);
    private static final double K9  = 2.0 / (9  + 1);

    public void update(double high, double low, double close) {
        long stamp = lock.writeLock();
        try {
            count++;
            // --- EMA 20 & 50 ---
            ema20 = Double.isNaN(ema20) ? close : close * K20 + ema20 * (1 - K20);
            ema50 = Double.isNaN(ema50) ? close : close * K50 + ema50 * (1 - K50);

            // --- MACD ---
            ema12 = Double.isNaN(ema12) ? close : close * K12 + ema12 * (1 - K12);
            ema26 = Double.isNaN(ema26) ? close : close * K26 + ema26 * (1 - K26);
            double macdLine = ema12 - ema26;
            macdSignal = Double.isNaN(macdSignal) ? macdLine : macdLine * K9 + macdSignal * (1 - K9);

            // --- ATR(14) Wilder ---
            if (!Double.isNaN(prevClose)) {
                double tr = Math.max(high - low,
                            Math.max(Math.abs(high - prevClose), Math.abs(low - prevClose)));
                if (atrCount < 14) {
                    atr14 = Double.isNaN(atr14) ? tr : atr14 + tr;
                    atrCount++;
                    if (atrCount == 14) atr14 /= 14.0;
                } else {
                    atr14 = (atr14 * 13 + tr) / 14.0;
                }
            }
            prevClose = close;

            // --- Bollinger Bands(20) ---
            bbWindow[bbHead] = close;
            bbHead = (bbHead + 1) % BB_PERIOD;
            if (bbFilled < BB_PERIOD) bbFilled++;
            if (bbFilled == BB_PERIOD) {
                double sum = 0;
                for (double v : bbWindow) sum += v;
                bbSMA = sum / BB_PERIOD;
                double varSum = 0;
                for (double v : bbWindow) varSum += (v - bbSMA) * (v - bbSMA);
                bbStdDev = Math.sqrt(varSum / BB_PERIOD);
                bbUpper  = bbSMA + 2 * bbStdDev;
                bbLower  = bbSMA - 2 * bbStdDev;
            }

            // --- SuperTrend ---
            if (!Double.isNaN(atr14) && atrCount >= 14) {
                double mid       = (high + low) / 2.0;
                double upperBand = mid + ST_MULT * atr14;
                double lowerBand = mid - ST_MULT * atr14;
                if (Double.isNaN(superTrend)) {
                    superTrend = lowerBand;
                    stDir = 1;
                } else if (stDir == 1) {
                    superTrend = Math.max(lowerBand, superTrend);
                    if (close < superTrend) { stDir = -1; superTrend = upperBand; }
                } else {
                    superTrend = Math.min(upperBand, superTrend);
                    if (close > superTrend) { stDir = 1; superTrend = lowerBand; }
                }
            }
        } finally {
            lock.unlockWrite(stamp);
        }
    }

    // ---- Read accessors (optimistic) ----
    public double getEma20()       { return read(() -> ema20); }
    public double getEma50()       { return read(() -> ema50); }
    public double getMacdLine()    { return read(() -> ema12 - ema26); }
    public double getMacdSignal()  { return read(() -> macdSignal); }
    public double getMacdHist()    { double l = getMacdLine(); double s = getMacdSignal(); return (!Double.isNaN(l) && !Double.isNaN(s)) ? l - s : Double.NaN; }
    public double getAtr14()       { return read(() -> atr14); }
    public double getBbUpper()     { return read(() -> bbUpper); }
    public double getBbLower()     { return read(() -> bbLower); }
    public double getBbSma()       { return read(() -> bbSMA); }
    public double getBbBandwidth() { double u = getBbUpper(), l = getBbLower(), m = getBbSma(); return (m > 0) ? (u - l) / m * 100 : Double.NaN; }
    public double getSuperTrend()  { return read(() -> superTrend); }
    public int    getSuperTrendDir() { long s = lock.tryOptimisticRead(); int v = stDir; return lock.validate(s) ? v : (int)lock.readLock(); }

    private double read(java.util.function.DoubleSupplier fn) {
        long s = lock.tryOptimisticRead();
        double v = fn.getAsDouble();
        if (!lock.validate(s)) {
            s = lock.readLock();
            try { v = fn.getAsDouble(); } finally { lock.unlockRead(s); }
        }
        return v;
    }
}
