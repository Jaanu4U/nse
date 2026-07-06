package com.nse.ingest.api;

import com.nse.ingest.domain.DailySummary;
import com.nse.ingest.domain.MinuteCandle;
import com.nse.ingest.dto.PredictionResultDto;
import com.nse.ingest.dto.SymbolSnapshotDto;
import com.nse.ingest.engine.PredictionEngine;
import com.nse.ingest.kite.WatchlistProvider;
import com.nse.ingest.repo.DailySummaryRepository;
import com.nse.ingest.repo.MinuteCandleRepository;
import com.nse.ingest.service.MarketStateRegistry;
import com.nse.ingest.service.SymbolState;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;

@RestController
@RequestMapping("/api/market")
public class MarketDataController {

    private final MarketStateRegistry    registry;
    private final PredictionEngine       predEngine;
    private final DailySummaryRepository dailyRepo;
    private final MinuteCandleRepository candleRepo;
    private final WatchlistProvider      watchlist;

    public MarketDataController(MarketStateRegistry registry,
                                 PredictionEngine predEngine,
                                 DailySummaryRepository dailyRepo,
                                 MinuteCandleRepository candleRepo,
                                 WatchlistProvider watchlist) {
        this.registry   = registry;
        this.predEngine = predEngine;
        this.dailyRepo  = dailyRepo;
        this.candleRepo = candleRepo;
        this.watchlist  = watchlist;
    }

    /** GET /api/market/snapshot/{symbol} — live in-memory snapshot */
    @GetMapping("/snapshot/{symbol}")
    public ResponseEntity<SymbolSnapshotDto> snapshot(@PathVariable String symbol) {
        SymbolState state = registry.get(symbol.toUpperCase());
        if (state == null) return ResponseEntity.notFound().build();
        PredictionResultDto pred = predEngine.score(state);
        return ResponseEntity.ok(new SymbolSnapshotDto(
            state.getSymbol(), state.getLtp(),
            state.getOpen(), state.getHigh(), state.getLow(), state.getLtp(),
            state.getVwap(), state.getTotalVolume(), state.getTradeCount(),
            state.getDeltaEngine().getBuyVolume(),
            state.getDeltaEngine().getSellVolume(),
            state.getDeltaEngine().getDelta(),
            state.getDeltaEngine().getCumulativeDelta(),
            state.getOrderBookImbalance(), state.getRsi(),
            pred.predictionScore(),
            state.getHistoricalUpProb(), state.getMlUpProb(),
            state.getExpectedMove(), state.getExpectedTarget(), state.isAbsorption(),
            LocalDateTime.now()
        ));
    }

    /** GET /api/market/snapshots — all tracked symbols */
    @GetMapping("/snapshots")
    public ResponseEntity<List<SymbolSnapshotDto>> allSnapshots() {
        List<SymbolSnapshotDto> list = new ArrayList<>();
        for (SymbolState state : registry.all()) {
            if (state.getTotalVolume() == 0) continue;
            PredictionResultDto pred = predEngine.score(state);
            list.add(new SymbolSnapshotDto(
                state.getSymbol(), state.getLtp(),
                state.getOpen(), state.getHigh(), state.getLow(), state.getLtp(),
                state.getVwap(), state.getTotalVolume(), state.getTradeCount(),
                state.getDeltaEngine().getBuyVolume(),
                state.getDeltaEngine().getSellVolume(),
                state.getDeltaEngine().getDelta(),
                state.getDeltaEngine().getCumulativeDelta(),
                state.getOrderBookImbalance(), state.getRsi(),
                pred.predictionScore(),
                state.getHistoricalUpProb(), state.getMlUpProb(),
                state.getExpectedMove(), state.getExpectedTarget(), state.isAbsorption(),
                LocalDateTime.now()
            ));
        }
        list.sort(Comparator.comparingDouble(SymbolSnapshotDto::predictionScore).reversed());
        return ResponseEntity.ok(list);
    }

    /** GET /api/market/prediction/{symbol} — prediction detail */
    @GetMapping("/prediction/{symbol}")
    public ResponseEntity<PredictionResultDto> prediction(@PathVariable String symbol) {
        SymbolState state = registry.get(symbol.toUpperCase());
        if (state == null) return ResponseEntity.notFound().build();
        return ResponseEntity.ok(predEngine.score(state));
    }

    /** GET /api/market/daily/{symbol}?date=2026-07-03 */
    @GetMapping("/daily/{symbol}")
    public ResponseEntity<Optional<DailySummary>> daily(
            @PathVariable String symbol,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate date) {
        return ResponseEntity.ok(dailyRepo.findByTradeDateAndSymbol(date, symbol.toUpperCase()));
    }

    /** GET /api/market/candles/{symbol}?date=2026-07-03 */
    @GetMapping("/candles/{symbol}")
    public ResponseEntity<List<MinuteCandle>> candles(
            @PathVariable String symbol,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate date) {
        return ResponseEntity.ok(candleRepo.findBySymbolAndTradeDateOrderByMinuteTsAsc(
            symbol.toUpperCase(), date));
    }

    /** GET /api/market/stats — global ingest stats */
    @GetMapping("/stats")
    public ResponseEntity<Map<String, Object>> stats() {
        return ResponseEntity.ok(Map.of(
            "symbols_tracked", registry.symbolCount(),
            "total_ticks",     registry.totalTicksProcessed(),
            "timestamp",       LocalDateTime.now()
        ));
    }

    /**
     * GET /api/market/watchlist
     * Returns the full list of stocks that get live delta from Kite.
     * These are all is_active=true rows in the stocks table.
     */
    @GetMapping("/watchlist")
    public ResponseEntity<Map<String, Object>> watchlist() {
        Map<String, String> active = watchlist.loadActiveStocks();
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map.Entry<String, String> e : active.entrySet()) {
            String sym = e.getKey();
            SymbolState state = registry.get(sym);
            boolean live = state != null && state.getTotalVolume() > 0;
            rows.add(Map.of(
                "symbol",      sym,
                "company_name", e.getValue(),
                "live",        live,
                "ltp",         live ? state.getLtp() : 0.0,
                "volume",      live ? state.getTotalVolume() : 0L
            ));
        }
        return ResponseEntity.ok(Map.of(
            "total",    active.size(),
            "live_now", rows.stream().filter(r -> Boolean.TRUE.equals(r.get("live"))).count(),
            "stocks",   rows
        ));
    }
}
