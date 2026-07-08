package com.nse.ingest.service;

import com.nse.ingest.domain.PriceBar;
import com.nse.ingest.domain.MinuteCandle;
import com.nse.ingest.engine.IndicatorEngine;
import com.nse.ingest.kite.KiteHistoricalService;
import com.nse.ingest.kite.WatchlistProvider;
import com.nse.ingest.repo.MinuteCandleRepository;
import com.nse.ingest.repo.PriceBarRepository;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Orchestrates the full 3-month historical data backfill from Kite.
 *
 * Steps per symbol:
 *  1. Check max(trade_date) in delta_prices_daily — skip if already up-to-date.
 *  2. Fetch missing bars from Kite historical API (rate-limited at 3 req/s).
 *  3. Batch-insert into delta_prices_daily.
 *  4. Re-feed all stored bars into the symbol's IndicatorEngine so ATR/EMA/MACD/RSI
 *     start from a statistically meaningful state when live ticks arrive.
 *
 * Run via POST /api/historical/load  (idempotent — safe to run multiple times).
 */
@Service
public class HistoricalDataLoader {

    private static final Logger log = LoggerFactory.getLogger(HistoricalDataLoader.class);
    private static final int BATCH_SIZE    = 100;
    private static final int THREAD_COUNT  = 8;  // keep below rate limit headroom

    private final KiteHistoricalService   kiteHist;
    private final PriceBarRepository      barRepo;
    private final MinuteCandleRepository  candleRepo;
    private final MarketStateRegistry     stateRegistry;
    private final WatchlistProvider       watchlist;
    private final Counter                 barsLoaded;

    // Loader progress (for /status endpoint)
    private volatile String  status     = "IDLE";
    private volatile int     total      = 0;
    private volatile int     done       = 0;
    private volatile int     failed     = 0;
    private volatile boolean running    = false;

    public HistoricalDataLoader(KiteHistoricalService kiteHist,
                                 PriceBarRepository barRepo,
                                 MinuteCandleRepository candleRepo,
                                 MarketStateRegistry stateRegistry,
                                 WatchlistProvider watchlist,
                                 MeterRegistry meterRegistry) {
        this.kiteHist      = kiteHist;
        this.barRepo       = barRepo;
        this.candleRepo    = candleRepo;
        this.stateRegistry = stateRegistry;
        this.watchlist     = watchlist;
        this.barsLoaded    = Counter.builder("ingest.historical.bars.loaded")
                .description("Historical price bars loaded from Kite")
                .register(meterRegistry);
    }

    /** Async trigger — returns immediately; progress via getStatus(). */
    public void startAsync(int months) {
        if (running) {
            log.warn("Historical load already in progress");
            return;
        }
        Thread.ofVirtual().start(() -> load(months));
    }

    /** Synchronous load — blocks until complete. */
    public LoadResult load(int months) {
        running = true;
        status  = "RUNNING";
        LocalDate to   = LocalDate.now();
        LocalDate from = to.minusMonths(months);
        List<String> symbols = watchlist.activeSymbols();
        total = symbols.size();
        done  = 0; failed = 0;
        log.info("Starting historical load: {} symbols, {} → {}", symbols.size(), from, to);

        ExecutorService pool = Executors.newFixedThreadPool(THREAD_COUNT,
            Thread.ofVirtual().factory());
        AtomicInteger barsTotal = new AtomicInteger(0);
        List<Future<Integer>> futures = new ArrayList<>();

        for (String sym : symbols) {
            futures.add(pool.submit(() -> loadSymbol(sym, from, to)));
        }
        pool.shutdown();

        for (Future<Integer> f : futures) {
            try {
                int bars = f.get();
                barsTotal.addAndGet(bars);
                done++;
            } catch (Exception e) {
                failed++;
                log.error("Load future failed", e);
            }
        }

        // After all data is fetched, pre-warm IndicatorEngines
        log.info("Pre-warming IndicatorEngine for {} symbols...", symbols.size());
        warmIndicators(symbols);

        status  = "DONE";
        running = false;
        log.info("Historical load complete: {} bars across {} symbols ({} failed)",
            barsTotal.get(), done, failed);
        return new LoadResult(done, failed, barsTotal.get(), from, to);
    }

    /** Load one symbol — fetch missing bars, persist, return bar count. */
    @Transactional
    public int loadSymbol(String symbol, LocalDate from, LocalDate to) {
        try {
            // Find the latest bar we already have
            LocalDate latestStored = barRepo.maxDate(symbol);
            LocalDate fetchFrom = (latestStored != null) ? latestStored.plusDays(1) : from;
            if (!fetchFrom.isBefore(to)) return 0; // already up-to-date

            List<KiteHistoricalService.OhlcvBar> bars = kiteHist.fetchDaily(symbol, fetchFrom, to);
            if (bars.isEmpty()) return 0;

            List<PriceBar> entities = new ArrayList<>();
            for (var b : bars) {
                PriceBar pb = new PriceBar();
                pb.setSymbol(symbol);
                pb.setTradeDate(b.date());
                pb.setOpenPrice(bd(b.open()));
                pb.setHighPrice(bd(b.high()));
                pb.setLowPrice(bd(b.low()));
                pb.setClosePrice(bd(b.close()));
                pb.setVolume(b.volume());
                pb.setSource("KITE");
                entities.add(pb);
            }
            // Batch save
            for (int i = 0; i < entities.size(); i += BATCH_SIZE) {
                barRepo.saveAll(entities.subList(i, Math.min(i + BATCH_SIZE, entities.size())));
            }
            barsLoaded.increment(bars.size());
            log.debug("Loaded {} bars for {}", bars.size(), symbol);
            return bars.size();
        } catch (Exception e) {
            log.error("Failed to load {}: {}", symbol, e.getMessage());
            return 0;
        }
    }

    /**
     * Feed all stored bars into each symbol's IndicatorEngine.
     * This ensures EMA/ATR/MACD/RSI converge BEFORE the first live tick arrives.
     *
     * Called automatically after the bulk load, and at 09:00 IST on market open
     * so the engine starts from the previous day's close.
     */
    public void warmIndicators(List<String> symbols) {
        for (String sym : symbols) {
            try {
                List<PriceBar> bars = barRepo.findBySymbolOrderByTradeDateAsc(sym);
                if (bars.isEmpty()) continue;
                SymbolState state = stateRegistry.getOrCreate(sym);
                IndicatorEngine ind = state.getIndicators();
                // Feed each historical bar — indicators are updated with H/L/C per bar
                for (PriceBar b : bars) {
                    ind.update(
                        b.getHighPrice().doubleValue(),
                        b.getLowPrice().doubleValue(),
                        b.getClosePrice().doubleValue()
                    );
                }
            } catch (Exception e) {
                log.warn("Indicator warm failed for {}: {}", sym, e.getMessage());
            }
        }
        log.info("IndicatorEngine pre-warmed for {} symbols", symbols.size());
    }

    public record LoadResult(int loaded, int failed, int totalBars,
                             LocalDate from, LocalDate to) {}

    public record GapFillResult(int symbols, int candles,
                                LocalDateTime from, LocalDateTime to) {}

    /**
     * Rebuild the in-memory SymbolState registry from today's stored
     * delta_minute_candle rows.  Called at startup so the dashboard shows the
     * day's data after any restart (including after market close, when no live
     * ticks will arrive to repopulate state).  Unlike the Kite gap-fill, DB rows
     * carry the real buy/sell delta split captured live.
     *
     * @return number of symbols rehydrated
     */
    public int rehydrateFromDb() {
        java.time.ZonedDateTime nowIst = java.time.ZonedDateTime.now(java.time.ZoneId.of("Asia/Kolkata"));
        LocalDate today = nowIst.toLocalDate();
        java.time.LocalTime t = nowIst.toLocalTime();
        // During the live session, per-minute delta counters must stay live-only;
        // when closed they can safely display the day's totals.
        boolean marketClosed = t.isBefore(java.time.LocalTime.of(9, 15))
                            || t.isAfter(java.time.LocalTime.of(15, 31));
        int count = 0;
        try {
            List<Object[]> rows = candleRepo.aggregateDayBySymbol(today);
            for (Object[] r : rows) {
                String symbol   = (String) r[0];
                double open     = ((Number) r[1]).doubleValue();
                double high     = ((Number) r[2]).doubleValue();
                double low      = ((Number) r[3]).doubleValue();
                double close    = ((Number) r[4]).doubleValue();
                long   volume   = ((Number) r[5]).longValue();
                long   buyVol   = ((Number) r[6]).longValue();
                long   sellVol  = ((Number) r[7]).longValue();
                double vwapNum  = ((Number) r[8]).doubleValue();
                SymbolState state = stateRegistry.getOrCreate(symbol);
                if (state.getTotalVolume() > 0) continue; // already has live state
                state.rehydrateDay(open, high, low, close, volume, buyVol, sellVol, vwapNum, marketClosed);
                count++;
            }
            log.info("[REHYDRATE] Restored day state for {} symbols from delta_minute_candle ({})",
                count, today);
        } catch (Exception e) {
            log.error("[REHYDRATE] Failed: {}", e.getMessage());
        }
        return count;
    }

    /**
     * Fetch today's missing minute candles from Kite for the gap period and apply them
     * to the in-memory SymbolState (volume, VWAP, price) and persist to delta_minute_candle.
     * Call this after a restart during market hours to repair the intraday data gap.
     *
     * @param fromIst start of the gap in IST (e.g. 09:15 at market open)
     * @param toIst   end of the gap in IST (typically just before the first live candle)
     */
    public GapFillResult replayIntradayGap(LocalDateTime fromIst, LocalDateTime toIst) {
        List<String> symbols = watchlist.activeSymbols();
        log.info("[GAP-FILL] Replaying {} → {} for {} symbols", fromIst, toIst, symbols.size());
        status  = "RUNNING";
        running = true;
        total   = symbols.size();
        done    = 0;
        failed  = 0;

        LocalDate today = fromIst.toLocalDate();

        // Per-symbol latest stored minute — avoids inserting duplicate candles when
        // the DB already has live candles for part of the replay window.
        Map<String, LocalDateTime> lastStored = new java.util.HashMap<>();
        try {
            for (Object[] row : candleRepo.maxMinuteTsBySymbol(today)) {
                lastStored.put((String) row[0], (LocalDateTime) row[1]);
            }
        } catch (Exception e) {
            log.warn("[GAP-FILL] Could not load existing max timestamps: {}", e.getMessage());
        }

        AtomicInteger candleCount = new AtomicInteger(0);
        ExecutorService pool = Executors.newFixedThreadPool(THREAD_COUNT,
            Thread.ofVirtual().factory());
        List<Future<?>> futures = new ArrayList<>();

        for (String symbol : symbols) {
            futures.add(pool.submit(() -> {
                try {
                    List<KiteHistoricalService.MinuteBar> bars =
                        kiteHist.fetchMinute(symbol, fromIst, toIst);
                    if (bars.isEmpty()) { done++; return; }

                    SymbolState state = stateRegistry.getOrCreate(symbol);
                    List<MinuteCandle> candles = new ArrayList<>();
                    long cumVol = state.getTotalVolume(); // already-accumulated live volume
                    LocalDateTime symbolLast = lastStored.get(symbol); // null = nothing stored today

                    for (var bar : bars) {
                        // TZ FIX: bar.dateTime() is IST wall-time, but the live
                        // aggregator stores JVM-local (UTC in container) wall-time.
                        // Convert IST → UTC so replayed rows align with live rows
                        // instead of landing +5:30 in the future.
                        LocalDateTime tsUtc = bar.dateTime()
                            .atZone(java.time.ZoneId.of("Asia/Kolkata"))
                            .withZoneSameInstant(java.time.ZoneOffset.UTC)
                            .toLocalDateTime();

                        // Skip minutes already stored in DB (live candles pre-crash).
                        // Their volume/delta is already in SymbolState via the
                        // startup rehydrateFromDb() — replaying again would double-count.
                        if (symbolLast != null && !tsUtc.isAfter(symbolLast)) continue;

                        state.replayIntradayGap(
                            bar.open(), bar.high(), bar.low(), bar.close(), bar.volume());
                        cumVol += bar.volume();

                        MinuteCandle c = new MinuteCandle();
                        c.setSymbol(symbol);
                        c.setTradeDate(today);
                        c.setMinuteTs(tsUtc);
                        c.setOpenPrice(bd(bar.open()));
                        c.setHighPrice(bd(bar.high()));
                        c.setLowPrice(bd(bar.low()));
                        c.setClosePrice(bd(bar.close()));
                        c.setVolume(bar.volume());
                        c.setDayVolume(cumVol);
                        c.setBuyVolume(0L);
                        c.setSellVolume(0L);
                        c.setDelta(0L);
                        candles.add(c);
                    }

                    // Batch persist — ON CONFLICT DO NOTHING via unique index
                    for (int i = 0; i < candles.size(); i += BATCH_SIZE) {
                        try {
                            candleRepo.saveAll(
                                candles.subList(i, Math.min(i + BATCH_SIZE, candles.size())));
                        } catch (Exception e) {
                            log.debug("[GAP-FILL] Some candles for {} already exist (skipped)", symbol);
                        }
                    }
                    candleCount.addAndGet(bars.size());
                    done++;
                } catch (Exception e) {
                    log.warn("[GAP-FILL] Failed for {}: {}", symbol, e.getMessage());
                    failed++;
                }
            }));
        }
        pool.shutdown();
        try { pool.awaitTermination(10, TimeUnit.MINUTES); } catch (InterruptedException ignored) {}

        status  = "DONE";
        running = false;
        log.info("[GAP-FILL] Done: {} symbols filled, {} candles inserted ({} failed)",
            done, candleCount.get(), failed);
        return new GapFillResult(done, candleCount.get(), fromIst, toIst);
    }

    public Map<String, Object> getStatus() {
        return Map.of(
            "status",   status,
            "running",  running,
            "total",    total,
            "done",     done,
            "failed",   failed,
            "pct",      total > 0 ? Math.round((double) done / total * 100) : 0
        );
    }

    private static BigDecimal bd(double v) {
        return BigDecimal.valueOf(v).setScale(4, RoundingMode.HALF_UP);
    }
}
