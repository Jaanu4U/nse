package com.nse.ingest.scheduler;

import com.nse.ingest.config.KiteProperties;
import com.nse.ingest.kite.InstrumentTokenRegistry;
import com.nse.ingest.kite.KiteAuthService;
import com.nse.ingest.kite.KiteTickerClient;
import com.nse.ingest.kite.WatchlistProvider;
import com.nse.ingest.service.EodService;
import com.nse.ingest.service.HistoricalDataLoader;
import com.nse.ingest.service.MinuteCandleAggregator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Drives the full market day lifecycle via cron expressions (IST = UTC+5:30).
 *
 * 09:00 IST = 03:30 UTC  → connect WebSocket
 * 09:15 IST = 03:45 UTC  → subscribe 400 instruments
 * Every minute            → flush minute candles
 * 15:30 IST = 10:00 UTC  → freeze calculations (stop new ticks from rolling windows)
 * 15:31 IST = 10:01 UTC  → persist EOD records
 * 15:35 IST = 10:05 UTC  → clear memory
 */
@Component
public class MarketScheduler {

    private static final Logger log = LoggerFactory.getLogger(MarketScheduler.class);

    private final KiteTickerClient        ticker;
    private final KiteAuthService         auth;
    private final KiteProperties          props;
    private final InstrumentTokenRegistry registry;
    private final WatchlistProvider       watchlist;
    private final EodService              eodService;
    private final MinuteCandleAggregator  aggregator;
    private final HistoricalDataLoader    histLoader;

    public MarketScheduler(KiteTickerClient ticker,
                            KiteAuthService auth,
                            KiteProperties props,
                            InstrumentTokenRegistry registry,
                            WatchlistProvider watchlist,
                            EodService eodService,
                            MinuteCandleAggregator aggregator,
                            HistoricalDataLoader histLoader) {
        this.ticker     = ticker;
        this.auth       = auth;
        this.props      = props;
        this.registry   = registry;
        this.watchlist  = watchlist;
        this.eodService = eodService;
        this.aggregator = aggregator;
        this.histLoader = histLoader;
    }

    /** 09:00 IST – pre-warm indicators from stored history, then connect WebSocket */
    @Scheduled(cron = "0 30 3 * * MON-FRI", zone = "UTC")
    public void connectWebSocket() {
        log.info("[SCHEDULER] 09:00 IST – warming indicators + connecting WebSocket");
        if (auth.isAuthenticated()) {
            registry.loadFromKite(auth.getAccessToken(), props.getApiKey());
            // Pre-warm IndicatorEngine from yesterday's stored bars so ATR/EMA/MACD
            // are statistically meaningful on the FIRST live tick of the day
            Thread.ofVirtual().start(() -> histLoader.warmIndicators(watchlist.activeSymbols()));
            ticker.connect();
        } else {
            log.warn("[SCHEDULER] Cannot connect: Kite not authenticated");
        }
    }

    /** 09:15 IST – subscribe active stocks loaded from the NSE stocks DB */
    @Scheduled(cron = "0 45 3 * * MON-FRI", zone = "UTC")
    public void subscribeInstruments() {
        log.info("[SCHEDULER] 09:15 IST – subscribing to DB-active instruments");
        // Load active symbols from the stocks table (same DB as Python backend)
        List<String> symbols = watchlist.activeSymbols();
        if (symbols.isEmpty()) {
            log.warn("[SCHEDULER] No active stocks found in DB — check stocks table");
            return;
        }
        // Resolve instrument tokens (loaded from Kite CSV during connectWebSocket)
        List<Integer> tokens = symbols.stream()
                .map(registry::token)
                .filter(java.util.Objects::nonNull)
                .toList();
        int missing = symbols.size() - tokens.size();
        if (missing > 0) {
            log.warn("[SCHEDULER] {} symbols not found in Kite instrument list (ETFs/SME excluded automatically)", missing);
        }
        if (tokens.isEmpty()) {
            log.warn("[SCHEDULER] No tokens resolved — ensure instrument registry is loaded");
            return;
        }
        ticker.subscribe(tokens);
        log.info("[SCHEDULER] Subscribed {} instruments ({} DB-active symbols)", tokens.size(), symbols.size());
    }

    /** Every minute during market hours – persist minute candles */
    @Scheduled(cron = "0 * 3-10 * * MON-FRI", zone = "UTC")
    public void flushMinuteCandles() {
        aggregator.flushMinuteCandles();
    }

    /** 15:30 IST – freeze / stop ticker */
    @Scheduled(cron = "0 0 10 * * MON-FRI", zone = "UTC")
    public void freezeMarket() {
        log.info("[SCHEDULER] 15:30 IST – freezing calculations");
        ticker.disconnect();
    }

    /** 15:31 IST – save EOD summary */
    @Scheduled(cron = "0 1 10 * * MON-FRI", zone = "UTC")
    public void saveEod() {
        log.info("[SCHEDULER] 15:31 IST – saving EOD");
        eodService.saveEod();
    }

    /** 15:35 IST – clear memory */
    @Scheduled(cron = "0 5 10 * * MON-FRI", zone = "UTC")
    public void cleanupMemory() {
        log.info("[SCHEDULER] 15:35 IST – clearing memory");
        eodService.cleanupMemory();
    }
}
