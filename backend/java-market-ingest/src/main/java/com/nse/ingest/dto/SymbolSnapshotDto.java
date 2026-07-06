package com.nse.ingest.dto;

import java.time.LocalDateTime;

/** Live in-memory snapshot sent over REST / WebSocket. */
public record SymbolSnapshotDto(
    String symbol,
    double ltp,
    double open,
    double high,
    double low,
    double close,
    double vwap,
    long   volume,
    int    trades,
    long   buyVolume,
    long   sellVolume,
    long   delta,
    long   cumulativeDelta,
    double orderBookImbalance,
    double rsi,
    double predictionScore,
    // Level 2 / 3 enriched fields
    double historicalUpProb,
    double mlUpProb,
    double expectedMove,
    double expectedTarget,
    boolean absorption,
    LocalDateTime snapshotAt
) {}
