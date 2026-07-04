package com.nse.ingest.service;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Service;

import java.util.Collection;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Central registry holding one {@link SymbolState} per active symbol.
 * Also tracks global counters for Micrometer Prometheus metrics.
 */
@Service
public class MarketStateRegistry {

    private final Map<String, SymbolState> states = new ConcurrentHashMap<>(400);
    private final AtomicLong ticksTotal            = new AtomicLong(0);
    private final Counter tickCounter;

    public MarketStateRegistry(MeterRegistry meterRegistry) {
        this.tickCounter = Counter.builder("ingest.ticks.processed")
                .description("Total ticks processed")
                .register(meterRegistry);
    }

    public SymbolState getOrCreate(String symbol) {
        return states.computeIfAbsent(symbol, SymbolState::new);
    }

    public SymbolState get(String symbol) {
        return states.get(symbol);
    }

    public Collection<SymbolState> all() {
        return states.values();
    }

    public void incrementTicks() {
        ticksTotal.incrementAndGet();
        tickCounter.increment();
    }

    public long totalTicksProcessed() { return ticksTotal.get(); }
    public int  symbolCount()         { return states.size(); }

    public void clearAll() {
        states.values().forEach(SymbolState::reset);
    }
}
