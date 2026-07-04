package com.nse.ingest.dto;

public record AlertRuleRequest(
    String  symbol,
    String  type,       // AlertEngine.AlertType name
    double  threshold,
    boolean once
) {}
