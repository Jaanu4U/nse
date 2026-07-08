package com.nse.ingest.api;

import com.nse.ingest.kite.WatchlistProvider;
import com.nse.ingest.repo.PriceBarRepository;
import com.nse.ingest.service.HistoricalDataLoader;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/historical")
public class HistoricalController {

    private final HistoricalDataLoader loader;
    private final PriceBarRepository   barRepo;
    private final WatchlistProvider    watchlist;

    public HistoricalController(HistoricalDataLoader loader,
                                 PriceBarRepository barRepo,
                                 WatchlistProvider watchlist) {
        this.loader    = loader;
        this.barRepo   = barRepo;
        this.watchlist = watchlist;
    }

    /**
     * POST /api/historical/load?months=3
     * Triggers full 3-month backfill from Kite asynchronously.
     * Returns immediately; poll /api/historical/status for progress.
     */
    @PostMapping("/load")
    public ResponseEntity<Map<String, Object>> load(
            @RequestParam(defaultValue = "3") int months) {
        loader.startAsync(months);
        return ResponseEntity.accepted().body(Map.of(
            "message", "Historical load started for " + watchlist.count() + " symbols",
            "months",  months,
            "status_url", "/api/historical/status"
        ));
    }

    /**
     * GET /api/historical/status
     * Returns progress of the ongoing or last load.
     */
    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> status() {
        return ResponseEntity.ok(loader.getStatus());
    }

    /**
     * GET /api/historical/bars/{symbol}?from=2026-04-01&to=2026-07-03
     * Returns stored historical bars for a symbol.
     */
    @GetMapping("/bars/{symbol}")
    public ResponseEntity<?> bars(
            @PathVariable String symbol,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to) {
        return ResponseEntity.ok(barRepo.findBySymbolAndTradeDateBetweenOrderByTradeDateAsc(
            symbol.toUpperCase(), from, to));
    }

    /**
     * GET /api/historical/summary
     * How many symbols and bars are loaded.
     */
    @GetMapping("/summary")
    public ResponseEntity<Map<String, Object>> summary() {
        return ResponseEntity.ok(Map.of(
            "symbols_with_data", barRepo.countDistinctSymbols(),
            "total_bars",        barRepo.count(),
            "active_symbols",    watchlist.count()
        ));
    }

    /**
     * POST /api/historical/warm-indicators
     * Re-feeds stored bars into IndicatorEngine (safe to call anytime).
     * Useful after restart to restore ATR/EMA/MACD state.
     */
    @PostMapping("/warm-indicators")
    public ResponseEntity<Map<String, String>> warmIndicators() {
        List<String> symbols = watchlist.activeSymbols();
        Thread.ofVirtual().start(() -> loader.warmIndicators(symbols));
        return ResponseEntity.accepted().body(Map.of(
            "message", "Warming indicators for " + symbols.size() + " symbols in background"
        ));
    }

    /**
     * POST /api/historical/replay-intraday?from=09:15&to=11:46
     *
     * Fetches today's missing minute candles from Kite for the gap window and:
     *   1. Patches each SymbolState with accumulated volume, VWAP, and price
     *   2. Persists the candles to delta_minute_candle for future avg-volume lookups
     *
     * Call this after a restart during market hours.
     * 'from' defaults to market open (09:15 IST).
     * 'to'   defaults to 5 minutes before now (give the live feed a head-start).
     */
    @PostMapping("/replay-intraday")
    public ResponseEntity<Map<String, Object>> replayIntraday(
            @RequestParam(defaultValue = "09:15") String from,
            @RequestParam(defaultValue = "")      String to) {
        ZoneId ist = ZoneId.of("Asia/Kolkata");
        LocalDate today = LocalDate.now(ist);
        DateTimeFormatter tf = DateTimeFormatter.ofPattern("HH:mm");
        LocalDateTime fromDt = LocalDateTime.of(today, LocalTime.parse(from, tf));
        LocalDateTime toDt   = to.isBlank()
            ? LocalDateTime.now(ist).minusMinutes(5)
            : LocalDateTime.of(today, LocalTime.parse(to, tf));

        Thread.ofVirtual().start(() -> loader.replayIntradayGap(fromDt, toDt));
        return ResponseEntity.accepted().body(Map.of(
            "message",     "Intraday gap fill started",
            "gap_from",    fromDt.toString(),
            "gap_to",      toDt.toString(),
            "symbols",     watchlist.count(),
            "status_url",  "/api/historical/status"
        ));
    }
}
