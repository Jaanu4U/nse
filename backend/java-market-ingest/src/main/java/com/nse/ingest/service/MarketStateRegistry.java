package com.nse.ingest.service;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.Collection;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Central registry holding one {@link SymbolState} per active symbol.
 * Also tracks global counters for Micrometer Prometheus metrics.
 */
@Service
public class MarketStateRegistry {

    private final Map<String, SymbolState> states = new ConcurrentHashMap<>(400);
    private final AtomicLong ticksTotal            = new AtomicLong(0);
    private final AtomicLong lastTickEpochMs       = new AtomicLong(0);
    private final AtomicReference<String> lastTickSymbol = new AtomicReference<>("");
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

    public void recordTick(String symbol) {
        ticksTotal.incrementAndGet();
        tickCounter.increment();
        lastTickEpochMs.set(System.currentTimeMillis());
        lastTickSymbol.set(symbol);
    }

    public long totalTicksProcessed() { return ticksTotal.get(); }
    public int  symbolCount()         { return states.size(); }
    public long lastTickEpochMs()      { return lastTickEpochMs.get(); }
    public String lastTickSymbol()     { return lastTickSymbol.get(); }
    public long lastTickAgeSeconds() {
        long ts = lastTickEpochMs.get();
        return ts == 0 ? -1 : Math.max(0L, (Instant.now().toEpochMilli() - ts) / 1000L);
    }

    public void clearAll() {
        states.values().forEach(SymbolState::reset);
    }
}
