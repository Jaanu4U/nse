package com.nse.ingest.dto;

import java.time.LocalDateTime;

public record AlertFiredDto(
    long          ruleId,
    String        symbol,
    String        type,
    double        value,
    double        threshold,
    LocalDateTime firedAt
) {}
