package com.nse.ingest.dto;

/** Parsed tick from Kite binary packet. */
public record TickDto(
    int    instrumentToken,
    String symbol,
    double ltp,
    long   lastTradedQty,
    double avgTradedPrice,
    long   volume,
    long   totalBuyQty,
    long   totalSellQty,
    double open,
    double high,
    double low,
    double close,
    double bestBidPrice,
    long   bestBidQty,
    double bestAskPrice,
    long   bestAskQty,
    long   timestampEpoch
) {}
