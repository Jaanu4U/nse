package com.nse.ingest.dto;

public record PredictionResultDto(
    String symbol,
    double bullishProbability,
    double bearishProbability,
    double confidenceScore,
    double predictionScore,
    String signal
) {}
