package com.nse.ingest.config;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import com.nse.ingest.service.MarketStateRegistry;
import org.springframework.context.annotation.Configuration;
import jakarta.annotation.PostConstruct;

@Configuration
public class MetricsConfig {

    private final MeterRegistry meterRegistry;
    private final MarketStateRegistry stateRegistry;

    public MetricsConfig(MeterRegistry meterRegistry, MarketStateRegistry stateRegistry) {
        this.meterRegistry = meterRegistry;
        this.stateRegistry = stateRegistry;
    }

    @PostConstruct
    public void registerGauges() {
        Gauge.builder("ingest.symbols.tracked", stateRegistry, r -> r.symbolCount())
                .description("Number of symbols currently being tracked in-memory")
                .register(meterRegistry);

        Gauge.builder("ingest.ticks.total", stateRegistry, r -> r.totalTicksProcessed())
                .description("Total number of ticks processed since application start")
                .register(meterRegistry);
    }
}
