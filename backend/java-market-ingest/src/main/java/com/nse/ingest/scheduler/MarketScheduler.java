package com.nse.ingest.scheduler;

import com.nse.ingest.config.KiteProperties;
import com.nse.ingest.kite.InstrumentTokenRegistry;
import com.nse.ingest.kite.KiteAuthService;
import com.nse.ingest.kite.KiteTickerClient;
import com.nse.ingest.kite.WatchlistProvider;
import com.nse.ingest.service.EodService;
import com.nse.ingest.service.HistoricalDataLoader;
import com.nse.ingest.service.MarketStateRegistry;
import com.nse.ingest.service.MinuteCandleAggregator;
import com.nse.ingest.service.PaperTradingEngine;
import com.nse.ingest.service.RegimeContext;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationStartedEvent;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;

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
    private final MarketStateRegistry     stateRegistry;
    private final JdbcTemplate            jdbc;
    private final PaperTradingEngine      paperTrading;

    /** Singleton regime context — refreshed at startup and 09:00 IST */
    public static final RegimeContext REGIME = new RegimeContext();

    public MarketScheduler(KiteTickerClient ticker,
                            KiteAuthService auth,
                            KiteProperties props,
                            InstrumentTokenRegistry registry,
                            WatchlistProvider watchlist,
                            EodService eodService,
                            MinuteCandleAggregator aggregator,
                            HistoricalDataLoader histLoader,
                            MarketStateRegistry stateRegistry,
                            JdbcTemplate jdbc,
                            PaperTradingEngine paperTrading) {
        this.ticker        = ticker;
        this.auth          = auth;
        this.props         = props;
        this.registry      = registry;
        this.watchlist     = watchlist;
        this.eodService    = eodService;
        this.aggregator    = aggregator;
        this.histLoader    = histLoader;
        this.stateRegistry = stateRegistry;
        this.jdbc          = jdbc;
        this.paperTrading  = paperTrading;
    }

    /**
     * Load per-minute avg volume from delta_minute_candle (last 20 trading days).
     * Also falls back to daily avg from prices_daily for symbols missing from candle table.
     * Called at startup and 09:00 IST so same-time-window deltaStrength is accurate immediately.
     */
    public void loadAvgVolumes() {
        // --- Step 1: per-minute same-time-window avg from delta_minute_candle ---
        // Epoch-gated to 2026-07-07: before that date `volume` was cumulative day
        // volume (wrong semantics) — see DATA_AUDIT_REPORT.md D2.
        try {
            String sql = """
                SELECT symbol,
                       EXTRACT(HOUR FROM minute_ts AT TIME ZONE 'Asia/Kolkata')::int * 60
                         + EXTRACT(MINUTE FROM minute_ts AT TIME ZONE 'Asia/Kolkata')::int AS minute_of_day,
                       AVG(volume)::bigint AS avg_vol
                FROM delta_minute_candle
                WHERE trade_date >= CURRENT_DATE - INTERVAL '22 days'
                  AND trade_date >= DATE '2026-07-07'
                  AND trade_date <  CURRENT_DATE
                GROUP BY symbol, minute_of_day
                """;
            java.util.Map<String, java.util.Map<Integer, Long>> bySymbol = new java.util.HashMap<>();
            for (java.util.Map<String, Object> row : jdbc.queryForList(sql)) {
                String sym = (String) row.get("symbol");
                Number mod  = (Number)  row.get("minute_of_day");
                Number vol  = (Number)  row.get("avg_vol");
                if (sym != null && mod != null && vol != null) {
                    bySymbol.computeIfAbsent(sym, k -> new java.util.HashMap<>())
                            .put(mod.intValue(), vol.longValue());
                }
            }
            int loaded = 0;
            for (java.util.Map.Entry<String, java.util.Map<Integer, Long>> e : bySymbol.entrySet()) {
                stateRegistry.getOrCreate(e.getKey()).setAvgVolumeByMinute(e.getValue());
                loaded++;
            }
            log.info("[AVG-VOL] Loaded per-minute same-time avg for {} symbols ({} minute rows)",
                     loaded, bySymbol.values().stream().mapToInt(java.util.Map::size).sum());
        } catch (Exception e) {
            log.warn("[AVG-VOL] Per-minute load failed: {}", e.getMessage());
        }

        // --- Step 2: daily avg fallback from prices_daily (for symbols not in candle table) ---
        try {
            String sql = """
                SELECT s.symbol, AVG(pd.volume)::bigint AS avg_vol
                FROM prices_daily pd
                JOIN stocks s ON s.id = pd.stock_id
                WHERE pd.timestamp >= CURRENT_DATE - INTERVAL '25 days'
                  AND pd.timestamp <  CURRENT_DATE
                GROUP BY s.symbol
                """;
            int fallback = 0;
            for (java.util.Map<String, Object> row : jdbc.queryForList(sql)) {
                String sym = (String) row.get("symbol");
                Long   vol = (Long)   row.get("avg_vol");
                if (sym != null && vol != null && vol > 0) {
                    stateRegistry.getOrCreate(sym).setAvgVolume20d(vol);
                    fallback++;
                }
            }
            log.info("[AVG-VOL] Loaded daily fallback avg for {} symbols", fallback);
        } catch (Exception e) {
            log.warn("[AVG-VOL] Daily fallback load failed: {}", e.getMessage());
        }
    }

    /**
     * Load Level 2 (historical profiles) and Level 3 (ML predictions) from DB into SymbolState.
     * Called at startup and 09:00 IST.
     */
    public void loadProfiles() {
        // --- Level 2: historical impact coefficients and probabilities ---
        try {
            String sql = """
                SELECT symbol, up_prob_overall, avg_move_15m, impact_coeff, bucket_stats
                FROM delta_stock_profiles
                WHERE impact_coeff IS NOT NULL
                """;
            int loaded = 0;
            for (java.util.Map<String, Object> row : jdbc.queryForList(sql)) {
                String sym = (String) row.get("symbol");
                Number upProb   = (Number) row.get("up_prob_overall");
                Number move15m  = (Number) row.get("avg_move_15m");
                Number coeff    = (Number) row.get("impact_coeff");
                String bucketsJson = row.get("bucket_stats") != null ? row.get("bucket_stats").toString() : null;
                if (sym != null) {
                    com.nse.ingest.service.SymbolState s = stateRegistry.getOrCreate(sym);
                    if (upProb  != null) s.setHistoricalUpProb(upProb.doubleValue());
                    if (move15m != null) s.setAvgMove15m(move15m.doubleValue());
                    if (coeff   != null) s.setImpactCoeff(coeff.doubleValue());
                    // C4: load per-bucket conditional up_probs
                    if (bucketsJson != null && !bucketsJson.isBlank()) {
                        try {
                            java.util.Map<String, Double> buckets = new java.util.HashMap<>();
                            com.fasterxml.jackson.databind.ObjectMapper om = new com.fasterxml.jackson.databind.ObjectMapper();
                            com.fasterxml.jackson.databind.JsonNode root = om.readTree(bucketsJson);
                            for (String key : new String[]{"weak","normal","strong","very_strong"}) {
                                com.fasterxml.jackson.databind.JsonNode node = root.path(key).path("up_prob");
                                if (!node.isMissingNode() && !node.isNull()) {
                                    buckets.put(key, node.asDouble());
                                }
                            }
                            if (!buckets.isEmpty()) s.setBucketUpProbs(buckets);
                        } catch (Exception je) {
                            log.debug("[PROFILES] bucket_stats parse error for {}: {}", sym, je.getMessage());
                        }
                    }
                    loaded++;
                }
            }
            log.info("[PROFILES] Loaded Level 2 profiles for {} symbols", loaded);
        } catch (Exception e) {
            log.warn("[PROFILES] Level 2 load failed: {}", e.getMessage());
        }
        // --- Level 3: ML predictions ---
        try {
            String sql = """
                SELECT symbol, ml_up_prob, ml_confidence
                FROM delta_ml_predictions
                WHERE ml_up_prob IS NOT NULL
                """;
            int loaded = 0;
            for (java.util.Map<String, Object> row : jdbc.queryForList(sql)) {
                String sym  = (String) row.get("symbol");
                Number prob = (Number) row.get("ml_up_prob");
                Number conf = (Number) row.get("ml_confidence");
                if (sym != null) {
                    com.nse.ingest.service.SymbolState s = stateRegistry.getOrCreate(sym);
                    if (prob != null) s.setMlUpProb(prob.doubleValue());
                    if (conf != null) s.setMlConfidence(conf.doubleValue());
                    loaded++;
                }
            }
            log.info("[PROFILES] Loaded Level 3 ML predictions for {} symbols", loaded);
        } catch (Exception e) {
            log.warn("[PROFILES] Level 3 load failed: {}", e.getMessage());
        }
    }

    /** Load today's regime context (VIX, expiry, gap day) from delta_regime_context */
    public void loadRegimeContext() {
        try {
            String sql = """
                SELECT india_vix, vix_percentile_20d, vix_level,
                       is_expiry_day, expiry_type, is_gap_day, gap_pct,
                       nifty_trend, regime_note
                FROM delta_regime_context
                WHERE trade_date = CURRENT_DATE
                """;
            var rows = jdbc.queryForList(sql);
            if (rows.isEmpty()) {
                log.info("[REGIME] No regime context for today — using defaults (Normal day)");
                return;
            }
            var row = rows.get(0);
            Number vix  = (Number) row.get("india_vix");
            Number pct  = (Number) row.get("vix_percentile_20d");
            Number gap  = (Number) row.get("gap_pct");
            if (vix  != null) REGIME.setIndiaVix(vix.doubleValue());
            if (pct  != null) REGIME.setVixPercentile(pct.doubleValue());
            if (gap  != null) REGIME.setGapPct(gap.doubleValue());
            REGIME.setVixLevel((String) row.get("vix_level"));
            REGIME.setExpiryDay(Boolean.TRUE.equals(row.get("is_expiry_day")));
            REGIME.setExpiryType((String) row.get("expiry_type"));
            REGIME.setGapDay(Boolean.TRUE.equals(row.get("is_gap_day")));
            REGIME.setNiftyTrend((String) row.get("nifty_trend"));
            REGIME.setRegimeNote((String) row.get("regime_note"));
            log.info("[REGIME] Loaded: {}", REGIME.getRegimeNote());
        } catch (Exception e) {
            log.warn("[REGIME] Regime context load failed: {}", e.getMessage());
        }
    }

    /** 09:00 IST – pre-warm indicators from stored history, then connect WebSocket */
    @Scheduled(cron = "0 30 3 * * MON-FRI", zone = "UTC")
    public void connectWebSocket() {
        log.info("[SCHEDULER] 09:00 IST – warming indicators + connecting WebSocket");
        loadAvgVolumes();  // load 20-day avg volume before ticks arrive
        loadProfiles();    // load L2/L3 profiles
        loadRegimeContext(); // load today's VIX/expiry/gap regime
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

    /**
     * On application startup: if KITE_ACCESS_TOKEN is already set in env/.env,
     * auto-load avg volumes and connect the WebSocket immediately.
     * This handles restarts during market hours without needing a manual auth call.
     */
    @EventListener(ApplicationStartedEvent.class)
    public void onStartup() {
        // Always rebuild today's in-memory state from stored minute candles so the
        // dashboard shows data after any restart (even after market close / without Kite auth).
        Thread.ofVirtual().start(histLoader::rehydrateFromDb);
        if (auth.isAuthenticated()) {
            log.info("[STARTUP] Kite token found — auto-connecting WebSocket");
            Thread.ofVirtual().start(() -> {
                try { Thread.sleep(3000); } catch (InterruptedException ignored) {}
                loadAvgVolumes();
                loadProfiles();
                loadRegimeContext();
                registry.loadFromKite(auth.getAccessToken(), props.getApiKey());
                // Warm EMA/ATR/MACD from stored daily bars (mid-day restart = cold indicators)
                Thread.ofVirtual().start(() -> histLoader.warmIndicators(watchlist.activeSymbols()));
                ticker.connect();
                try { Thread.sleep(3000); } catch (InterruptedException ignored) {}
                subscribeInstruments();
                autoGapFillIfMarketHours();
            });
        } else {
            log.info("[STARTUP] No Kite token — waiting for manual auth via /api/kite/access-token");
        }
    }

    /**
     * SELF-HEAL: if the app (re)starts during market hours, automatically replay
     * today's missed minute candles from Kite so volume/VWAP/price state and
     * delta_minute_candle are complete. No manual /replay-intraday call needed.
     */
    private void autoGapFillIfMarketHours() {
        java.time.ZoneId ist = java.time.ZoneId.of("Asia/Kolkata");
        java.time.ZonedDateTime now = java.time.ZonedDateTime.now(ist);
        java.time.DayOfWeek dow = now.getDayOfWeek();
        if (dow == java.time.DayOfWeek.SATURDAY || dow == java.time.DayOfWeek.SUNDAY) return;
        java.time.LocalTime t = now.toLocalTime();
        // Only relevant between 09:20 and 15:30 IST (before 09:20 there is nothing to fill)
        if (t.isBefore(java.time.LocalTime.of(9, 20)) || t.isAfter(java.time.LocalTime.of(15, 30))) return;

        java.time.LocalDateTime from = now.toLocalDate().atTime(9, 15);
        java.time.LocalDateTime to   = now.toLocalDateTime().minusMinutes(2);
        log.info("[STARTUP] Market hours restart detected — auto gap-filling {} → {}", from, to);
        try {
            histLoader.replayIntradayGap(from, to);
        } catch (Exception e) {
            log.error("[STARTUP] Auto gap-fill failed: {}", e.getMessage());
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

    /** Every minute during market hours – persist minute candles + run paper trading */
    @Scheduled(cron = "0 * 3-10 * * MON-FRI", zone = "UTC")
    public void flushMinuteCandles() {
        aggregator.flushMinuteCandles();
        // Paper trading tick — check entries/exits each minute
        try { paperTrading.tick(); } catch (Exception e) {
            log.warn("[PAPER] tick error: {}", e.getMessage());
        }
    }

    /**
     * ULTIMATE SELF-HEAL: every minute during market hours, verify real market
     * data is flowing (not just that the socket says "connected").
     * If no tick has been processed for FEED_STALE_SECONDS, force a full
     * WebSocket reconnect + resubscribe. Two consecutive stale checks with a
     * failed reconnect keep retrying each minute — no human needed.
     */
    private static final long FEED_STALE_SECONDS = 120;
    private volatile long lastFeedRecoveryEpochMs = 0;

    @Scheduled(cron = "30 * 3-9 * * MON-FRI", zone = "UTC")
    public void feedWatchdog() {
        java.time.ZoneId ist = java.time.ZoneId.of("Asia/Kolkata");
        java.time.LocalTime t = java.time.ZonedDateTime.now(ist).toLocalTime();
        // Active window: 09:16–15:29 IST (skip the open/close boundary minutes)
        if (t.isBefore(java.time.LocalTime.of(9, 16)) || t.isAfter(java.time.LocalTime.of(15, 29))) return;
        if (!auth.isAuthenticated()) return; // token watchdog handles this case

        long age = stateRegistry.lastTickAgeSeconds();
        boolean neverTicked = age < 0;
        boolean stale       = age >= FEED_STALE_SECONDS;
        if (!neverTicked && !stale) return; // healthy

        // Rate-limit recovery attempts to once per 2 minutes
        long now = System.currentTimeMillis();
        if (now - lastFeedRecoveryEpochMs < 120_000) return;
        lastFeedRecoveryEpochMs = now;

        log.warn("[FEED-WATCHDOG] Live feed stale (last tick {}s ago, connected={}) — forcing reconnect",
                 neverTicked ? "never" : String.valueOf(age), ticker.isConnected());
        try {
            ticker.disconnect();
            Thread.sleep(1500);
            ticker.connect();
            Thread.sleep(3000);
            subscribeInstruments();
            log.info("[FEED-WATCHDOG] Reconnect + resubscribe issued; will re-verify next minute");
        } catch (Exception e) {
            log.error("[FEED-WATCHDOG] Recovery attempt failed: {}", e.getMessage());
        }
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

    /**
     * Every 15 min, 09:30–15:15 IST – snapshot per-symbol predictions to
     * delta_prediction_history so the backend EOD accuracy job can resolve
     * 15-minute forward returns against minute candles.
     */
    @Scheduled(cron = "0 0,15,30,45 4-9 * * MON-FRI", zone = "UTC")
    public void snapshotPredictions() {
        try {
            eodService.snapshotPredictions();
        } catch (Exception e) {
            log.error("[SNAPSHOT] Prediction snapshot failed: {}", e.getMessage());
        }
    }

    /**
     * 08:50 IST – clear yesterday's in-memory tick state before the new session.
     * (Moved from 15:35 so the dashboard keeps showing the day's data all evening.)
     */
    @Scheduled(cron = "0 20 3 * * MON-FRI", zone = "UTC")
    public void preOpenMemoryReset() {
        log.info("[SCHEDULER] 08:50 IST – clearing yesterday's in-memory tick state");
        eodService.cleanupMemory();
    }

    /** 15:35 IST – purge old minute candles (keep 90 days) */
    @Scheduled(cron = "0 5 10 * * MON-FRI", zone = "UTC")
    public void cleanupMemory() {
        // NOTE: in-memory state is intentionally KEPT after close so the dashboard
        // still shows the day's data; it is cleared pre-open by preOpenMemoryReset().
        // Purge delta_minute_candle rows older than 90 days to cap storage at ~1.5 GB
        try {
            int deleted = jdbc.update(
                "DELETE FROM delta_minute_candle WHERE trade_date < CURRENT_DATE - INTERVAL '90 days'"
            );
            if (deleted > 0) log.info("[CLEANUP] Purged {} old minute candle rows (>90 days)", deleted);
        } catch (Exception e) {
            log.warn("[CLEANUP] Minute candle purge failed: {}", e.getMessage());
        }
    }

    /**
     * 08:45 IST (03:15 UTC) Mon-Fri — Kite token health check.
     * Logs a WARN (visible in dashboards/alerts) if the token has expired so the
     * day doesn't silently degrade with no live data.
     */
    @Scheduled(cron = "0 15 3 * * MON-FRI", zone = "UTC")
    public void kiteTokenHealthCheck() {
        if (!auth.isAuthenticated()) {
            log.warn("[KITE-HEALTH] *** Kite access token is MISSING or EXPIRED at 08:45 IST. " +
                     "Live ticks will not flow today. " +
                     "Please authenticate via POST /api/kite/access-token before market open (09:15 IST). ***");
        } else {
            log.info("[KITE-HEALTH] Kite token present at 08:45 IST — OK");
        }
    }
}
