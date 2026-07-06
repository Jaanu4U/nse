package com.nse.ingest.service;

/**
 * Today's market regime context loaded from delta_regime_context at startup.
 * Used by PredictionEngine to apply confidence multipliers.
 */
public class RegimeContext {

    private volatile double  indiaVix         = 0.0;
    private volatile double  vixPercentile    = 50.0; // default normal
    private volatile String  vixLevel         = "NORMAL"; // LOW/NORMAL/HIGH/EXTREME
    private volatile boolean isExpiryDay      = false;
    private volatile String  expiryType       = null;  // WEEKLY / MONTHLY
    private volatile boolean isGapDay         = false;
    private volatile double  gapPct           = 0.0;
    private volatile String  niftyTrend       = "FLAT"; // UP / DOWN / FLAT
    private volatile String  regimeNote       = "Normal day";

    /** Confirmation multiplier based on current regime (applied to expectedMove) */
    public double getConfirmationMultiplier() {
        double mult = 1.0;
        // High VIX → reduce confidence (signals less reliable)
        if ("EXTREME".equals(vixLevel))      mult *= 0.60;
        else if ("HIGH".equals(vixLevel))    mult *= 0.80;
        else if ("LOW".equals(vixLevel))     mult *= 1.10; // calm market, more reliable
        // Expiry day → more volatile, reduce confidence slightly
        if (isExpiryDay)                     mult *= 0.85;
        // Large gap day → regime shift, reduce confidence
        if (isGapDay && Math.abs(gapPct) > 1.5) mult *= 0.75;
        return mult;
    }

    /** Whether current regime is "hostile" (high VIX + expiry or large gap) */
    public boolean isHostileRegime() {
        return ("HIGH".equals(vixLevel) || "EXTREME".equals(vixLevel))
            && (isExpiryDay || Math.abs(gapPct) > 1.0);
    }

    // Getters
    public double  getIndiaVix()       { return indiaVix; }
    public double  getVixPercentile()  { return vixPercentile; }
    public String  getVixLevel()       { return vixLevel; }
    public boolean isExpiryDay()       { return isExpiryDay; }
    public String  getExpiryType()     { return expiryType; }
    public boolean isGapDay()          { return isGapDay; }
    public double  getGapPct()         { return gapPct; }
    public String  getNiftyTrend()     { return niftyTrend; }
    public String  getRegimeNote()     { return regimeNote; }

    // Setters (called by MarketScheduler.loadRegimeContext)
    public void setIndiaVix(double v)       { this.indiaVix = v; }
    public void setVixPercentile(double v)  { this.vixPercentile = v; }
    public void setVixLevel(String v)       { this.vixLevel = v != null ? v : "NORMAL"; }
    public void setExpiryDay(boolean v)     { this.isExpiryDay = v; }
    public void setExpiryType(String v)     { this.expiryType = v; }
    public void setGapDay(boolean v)        { this.isGapDay = v; }
    public void setGapPct(double v)         { this.gapPct = v; }
    public void setNiftyTrend(String v)     { this.niftyTrend = v != null ? v : "FLAT"; }
    public void setRegimeNote(String v)     { this.regimeNote = v != null ? v : "Normal day"; }
}
