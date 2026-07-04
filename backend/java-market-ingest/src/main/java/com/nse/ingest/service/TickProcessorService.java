package com.nse.ingest.service;

import com.nse.ingest.dto.TickDto;
import com.nse.ingest.engine.AlertEngine;
import com.nse.ingest.engine.IndicatorEngine;
import io.micrometer.core.instrument.Timer;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * Core tick dispatcher.  Called from virtual threads (one per tick).
 *
 * Pipeline per tick (target < 200 µs total):
 *   1. getOrCreate SymbolState             (~50 ns — ConcurrentHashMap)
 *   2. acceptTick → LeeReady + Delta + OHLC/VWAP + RSI   (~80 µs)
 *   3. IndicatorEngine.update (already called inside acceptTick)
 *   4. AlertEngine.evaluate                (~10 µs)
 */
@Service
public class TickProcessorService {

    private static final Logger log = LoggerFactory.getLogger(TickProcessorService.class);

    private final MarketStateRegistry registry;
    private final AlertEngine         alertEngine;
    private final Timer               tickTimer;

    public TickProcessorService(MarketStateRegistry registry,
                                 AlertEngine alertEngine,
                                 MeterRegistry meterRegistry) {
        this.registry    = registry;
        this.alertEngine = alertEngine;
        this.tickTimer   = Timer.builder("ingest.tick.processing.time")
                .description("Per-tick processing latency")
                .publishPercentileHistogram()
                .register(meterRegistry);
    }

    public void process(TickDto tick) {
        Timer.Sample sample = Timer.start();
        try {
            SymbolState state = registry.getOrCreate(tick.symbol());
            state.acceptTick(
                tick.ltp(), tick.lastTradedQty(),
                tick.bestBidPrice(), tick.bestBidQty(),
                tick.bestAskPrice(), tick.bestAskQty()
            );
            registry.incrementTicks();

            // Evaluate alert rules against fresh state (all in-memory, ~10µs)
            IndicatorEngine ind = state.getIndicators();
            alertEngine.evaluate(
                tick.symbol(),
                tick.ltp(),
                state.getDeltaEngine().getDelta(),
                state.getTotalVolume(),
                state.getRsi(),
                ind.getMacdHist(),
                ind.getAtr14(),
                ind.getSuperTrendDir(),
                ind.getBbUpper(),
                ind.getBbLower()
            );
        } catch (Exception e) {
            log.error("Tick processing error: {}", tick.symbol(), e);
        } finally {
            sample.stop(tickTimer);
        }
    }
}
