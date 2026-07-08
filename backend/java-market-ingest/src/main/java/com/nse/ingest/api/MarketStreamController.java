package com.nse.ingest.api;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.nse.ingest.dto.AlertFiredDto;
import com.nse.ingest.dto.SymbolSnapshotDto;
import com.nse.ingest.engine.IndicatorEngine;
import com.nse.ingest.engine.PredictionEngine;
import com.nse.ingest.service.AlertService;
import com.nse.ingest.service.MarketStateRegistry;
import com.nse.ingest.service.SymbolState;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.LocalDateTime;
import java.util.*;
import java.util.concurrent.*;

/**
 * Server-Sent Events endpoint.  The React dashboard connects once via EventSource
 * and receives real-time market snapshots (every second) and instant alert events.
 *
 * GET /api/stream/market?symbols=RELIANCE,INFY  — snapshot stream
 * GET /api/stream/alerts                        — alert-only stream
 */
@RestController
@RequestMapping("/api/stream")
public class MarketStreamController {

    private static final Logger log = LoggerFactory.getLogger(MarketStreamController.class);
    private static final long   SSE_TIMEOUT_MS = 3_600_000L; // 1 hour

    private final MarketStateRegistry registry;
    private final PredictionEngine    predEngine;
    private final AlertService        alertService;
    private final ObjectMapper        mapper;

    // Active market emitters: clientId → emitter
    private final Map<String, SseEmitter> marketEmitters = new ConcurrentHashMap<>();
    private final Map<String, SseEmitter> alertEmitters  = new ConcurrentHashMap<>();

    private final ScheduledExecutorService scheduler =
        Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "sse-market-push");
            t.setDaemon(true);
            return t;
        });

    public MarketStreamController(MarketStateRegistry registry,
                                   PredictionEngine predEngine,
                                   AlertService alertService) {
        this.registry    = registry;
        this.predEngine  = predEngine;
        this.alertService = alertService;
        this.mapper = new ObjectMapper().registerModule(new JavaTimeModule());

        // Push market snapshots every second
        scheduler.scheduleAtFixedRate(this::pushMarketSnapshots, 1, 1, TimeUnit.SECONDS);

        // Register alert listener → push to alert SSE clients
        alertService.addListener(this::pushAlert);
    }

    /**
     * Market data stream.
     * ?symbols=RELIANCE,INFY  (optional; omit for all tracked symbols)
     */
    @GetMapping(value = "/market", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter marketStream(@RequestParam(required = false) String symbols) {
        SseEmitter emitter = new SseEmitter(SSE_TIMEOUT_MS);
        String clientId = UUID.randomUUID().toString();
        // Store requested symbols as client metadata via name (hack-safe enough)
        String emitterKey = clientId + "|" + (symbols != null ? symbols : "*");
        marketEmitters.put(emitterKey, emitter);
        emitter.onCompletion(() -> marketEmitters.remove(emitterKey));
        emitter.onTimeout(() -> marketEmitters.remove(emitterKey));
        emitter.onError(e -> marketEmitters.remove(emitterKey));
        scheduler.execute(() -> {
            try {
                emitter.send(SseEmitter.event().name("ready").data("connected"));
            } catch (IOException ignored) {
                // Let the normal completion/error callbacks remove the emitter.
            }
        });
        return emitter;
    }

    /**
     * Alert-only stream — receives only AlertFiredDto events, instantly.
     */
    @GetMapping(value = "/alerts", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter alertStream() {
        SseEmitter emitter = new SseEmitter(SSE_TIMEOUT_MS);
        String clientId = UUID.randomUUID().toString();
        alertEmitters.put(clientId, emitter);
        emitter.onCompletion(() -> alertEmitters.remove(clientId));
        emitter.onTimeout(() -> alertEmitters.remove(clientId));
        emitter.onError(e -> alertEmitters.remove(clientId));
        scheduler.execute(() -> {
            try {
                emitter.send(SseEmitter.event().name("ready").data("connected"));
            } catch (IOException ignored) {
                // Let the timeout/completion handlers clean up the emitter.
            }
        });
        return emitter;
    }

    // ---- Private internals --------------------------------------------------

    private void pushMarketSnapshots() {
        if (marketEmitters.isEmpty()) return;
        List<Map<String, Object>> all = buildSnapshots(null);
        List<String> toRemove = new ArrayList<>();
        for (Map.Entry<String, SseEmitter> e : marketEmitters.entrySet()) {
            String key     = e.getKey();
            String filter   = key.contains("|") ? key.split("\\|", 2)[1] : "*";
            String clientId = key.contains("|") ? key.split("\\|", 2)[0] : key;
            List<Map<String, Object>> payload = "*".equals(filter) ? all : filterBySymbols(all, filter);
            try {
                e.getValue().send(SseEmitter.event()
                    .name("market")
                    .data(mapper.writeValueAsString(payload)));
            } catch (IOException ex) {
                toRemove.add(key);
            }
        }
        toRemove.forEach(marketEmitters::remove);
    }

    private void pushAlert(AlertFiredDto alert) {
        alertService.onAlertFired(alert);  // persist to DB
        if (alertEmitters.isEmpty() && marketEmitters.isEmpty()) return;
        try {
            String json = mapper.writeValueAsString(alert);
            List<String> toRemove = new ArrayList<>();
            for (Map.Entry<String, SseEmitter> e : alertEmitters.entrySet()) {
                try { e.getValue().send(SseEmitter.event().name("alert").data(json)); }
                catch (IOException ex) { toRemove.add(e.getKey()); }
            }
            toRemove.forEach(alertEmitters::remove);
            // Also push alert to market emitters
            toRemove.clear();
            for (Map.Entry<String, SseEmitter> e : marketEmitters.entrySet()) {
                try { e.getValue().send(SseEmitter.event().name("alert").data(json)); }
                catch (IOException ex) { toRemove.add(e.getKey()); }
            }
            toRemove.forEach(marketEmitters::remove);
        } catch (Exception ex) {
            log.error("Alert push failed", ex);
        }
    }

    private List<Map<String, Object>> buildSnapshots(String filter) {
        List<Map<String, Object>> list = new ArrayList<>();
        for (SymbolState state : registry.all()) {
            if (state.getTotalVolume() == 0) continue;
            var pred = predEngine.score(state);
            IndicatorEngine ind = state.getIndicators();
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("symbol",         state.getSymbol());
            m.put("ltp",            round2(state.getLtp()));
            m.put("open",           round2(state.getOpen()));
            m.put("high",           round2(state.getHigh()));
            m.put("low",            round2(state.getLow()));
            m.put("vwap",           round2(state.getVwap()));
            m.put("volume",         state.getTotalVolume());
            m.put("buyVolume",      state.getDeltaEngine().getBuyVolume());
            m.put("sellVolume",     state.getDeltaEngine().getSellVolume());
            m.put("delta",          state.getDeltaEngine().getDelta());
            m.put("cumulativeDelta",state.getDeltaEngine().getCumulativeDelta());
            m.put("deltaStrength",  round2(state.getDeltaStrength()));
            m.put("volumeRatio",    round2(state.getVolumeRatio()));
            m.put("deltaPercent",   round2(state.getDeltaPercent()));
            m.put("obi",            round4(state.getOrderBookImbalance()));
            m.put("rsi",            round2(state.getRsi()));
            m.put("ema20",          round2(ind.getEma20()));
            m.put("ema50",          round2(ind.getEma50()));
            m.put("atr14",          round2(ind.getAtr14()));
            m.put("macdLine",       round4(ind.getMacdLine()));
            m.put("macdSignal",     round4(ind.getMacdSignal()));
            m.put("macdHist",       round4(ind.getMacdHist()));
            m.put("bbUpper",        round2(ind.getBbUpper()));
            m.put("bbLower",        round2(ind.getBbLower()));
            m.put("bbBandwidth",    round2(ind.getBbBandwidth()));
            m.put("superTrendDir",  ind.getSuperTrendDir());
            m.put("predScore",         round2(pred.predictionScore()));
            m.put("bullishProb",         round4(pred.bullishProbability()));
            m.put("signal",              pred.signal());
            // Level 2 / 3 enriched fields
            m.put("historicalUpProb",    round2(state.getHistoricalUpProb()));
            m.put("mlUpProb",            round4(state.getMlUpProb()));
            m.put("expectedMove",        round4(state.getExpectedMove()));
            m.put("expectedTarget",      round2(state.getExpectedTarget()));
            m.put("absorption",          state.isAbsorption());
            // Regime context (same for all symbols — today's market conditions)
            var regime = com.nse.ingest.scheduler.MarketScheduler.REGIME;
            m.put("vix",           round2(regime.getIndiaVix()));
            m.put("vixLevel",      regime.getVixLevel());
            m.put("isExpiryDay",   regime.isExpiryDay());
            m.put("isGapDay",      regime.isGapDay());
            m.put("niftyTrend",    regime.getNiftyTrend());
            m.put("regimeNote",    regime.getRegimeNote());
            m.put("ts",                  LocalDateTime.now().toString());
            list.add(m);
        }
        list.sort((a, b) -> Double.compare((double) b.getOrDefault("predScore", 0.0), (double) a.getOrDefault("predScore", 0.0)));
        return list;
    }

    private List<Map<String, Object>> filterBySymbols(List<Map<String, Object>> all, String filter) {
        Set<String> syms = new HashSet<>(Arrays.asList(filter.split(",")));
        return all.stream().filter(m -> syms.contains(m.get("symbol"))).toList();
    }

    private static double round2(double v) { return Double.isNaN(v) || Double.isInfinite(v) ? 0.0 : Math.round(v * 100.0) / 100.0; }
    private static double round4(double v) { return Double.isNaN(v) || Double.isInfinite(v) ? 0.0 : Math.round(v * 10000.0) / 10000.0; }

    @PreDestroy
    public void shutdown() { scheduler.shutdownNow(); }
}
