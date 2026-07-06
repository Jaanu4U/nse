package com.nse.ingest.service;

import com.nse.ingest.engine.PredictionEngine;
import com.nse.ingest.scheduler.MarketScheduler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Paper Trading Engine — runs every minute during market hours.
 *
 * Entry rules (LONG only):
 *   - predScore >= 65  (strong BULLISH)
 *   - deltaStrength >= 2.0%
 *   - historicalUpProb >= 52% OR mlUpProb >= 0.52
 *   - absorption == false
 *   - VIX level != EXTREME
 *   - No open position for this symbol today
 *   - Time between 09:20 and 14:55 IST
 *
 * Exit rules:
 *   - 30 minutes after entry (30-MIN target)
 *   - EOD at 15:20 IST
 *   - Signal reverses to BEARISH with predScore <= 35
 *
 * Qty: 100 fixed. P&L = (exitPrice - entryPrice) × 100.
 */
@Service
public class PaperTradingEngine {

    private static final Logger log = LoggerFactory.getLogger(PaperTradingEngine.class);

    private static final int    QTY             = 100;
    private static final double MIN_PRED_SCORE  = 65.0;
    private static final double MIN_DELTA_STR   = 2.0;
    // Calibrated to actual data distributions (see PREDICTION_FLOW_AUDIT.md C1):
    // up_prob_overall universe max ≈ 51.9 (mean 46) → 50 = top tail
    // ml_up_prob universe max ≈ 0.34 (mean 0.22, base rate 0.15) → 0.28 = top tail
    private static final double MIN_HIST_PROB   = 50.0;
    private static final double MIN_ML_PROB     = 0.28;
    private static final LocalTime ENTRY_START  = LocalTime.of(9, 20);
    private static final LocalTime ENTRY_END    = LocalTime.of(14, 55);
    private static final LocalTime EOD_EXIT     = LocalTime.of(15, 20);
    private static final int    EXIT_MINS       = 30;

    /** symbol → open trade id (DB id) */
    private final Map<String, Long> openPositions = new ConcurrentHashMap<>();
    /** symbol → entry time (epoch millis) */
    private final Map<String, Long> entryTimes    = new ConcurrentHashMap<>();
    /** symbol → entry price */
    private final Map<String, Double> entryPrices = new ConcurrentHashMap<>();
    /** Symbols traded today (to avoid re-entry same symbol same day) */
    private final Set<String> tradedToday        = ConcurrentHashMap.newKeySet();
    private volatile LocalDate lastResetDate     = LocalDate.now();

    private final MarketStateRegistry stateRegistry;
    private final PredictionEngine    predEngine;
    private final JdbcTemplate        jdbc;

    public PaperTradingEngine(MarketStateRegistry stateRegistry,
                               PredictionEngine predEngine,
                               JdbcTemplate jdbc) {
        this.stateRegistry = stateRegistry;
        this.predEngine    = predEngine;
        this.jdbc          = jdbc;
    }

    /**
     * Called every minute by MarketScheduler during market hours.
     * 1. Check open positions for exit
     * 2. Scan all symbols for new entry signals
     */
    public void tick() {
        ZoneId ist    = ZoneId.of("Asia/Kolkata");
        ZonedDateTime now = ZonedDateTime.now(ist);
        LocalTime time    = now.toLocalTime();

        // Reset daily state at market open
        LocalDate today = now.toLocalDate();
        if (!today.equals(lastResetDate)) {
            lastResetDate = today;
            openPositions.clear();
            entryTimes.clear();
            entryPrices.clear();
            tradedToday.clear();
            log.info("[PAPER] New trading day reset for {}", today);
        }

        // Force-close all open positions at EOD
        if (time.isAfter(EOD_EXIT)) {
            closeAllOpenPositions("EOD", now);
            return;
        }

        // Process open positions (check 30-min exit or reversal)
        processExits(now);

        // Scan for new entries (only during valid entry window)
        if (time.isAfter(ENTRY_START) && time.isBefore(ENTRY_END)) {
            scanForEntries(now, today);
        }
    }

    private void processExits(ZonedDateTime now) {
        for (String symbol : new ArrayList<>(openPositions.keySet())) {
            SymbolState state = stateRegistry.get(symbol);
            if (state == null || state.getLtp() <= 0) continue;

            long openId      = openPositions.get(symbol);
            long entryMillis = entryTimes.getOrDefault(symbol, 0L);
            double entryPrice = entryPrices.getOrDefault(symbol, 0.0);
            double exitPrice  = state.getLtp();

            long minutesSinceEntry = (now.toInstant().toEpochMilli() - entryMillis) / 60_000;
            String exitReason = null;

            if (minutesSinceEntry >= EXIT_MINS) {
                exitReason = "30MIN_TARGET";
            } else {
                // Check for signal reversal
                var pred = predEngine.score(state);
                if ("BEARISH".equals(pred.signal()) && pred.predictionScore() <= 35) {
                    exitReason = "REVERSAL";
                }
            }

            if (exitReason != null) {
                closeTrade(openId, symbol, exitPrice, now, exitReason);
            }
        }
    }

    private void scanForEntries(ZonedDateTime now, LocalDate today) {
        var regime = MarketScheduler.REGIME;
        if ("EXTREME".equals(regime.getVixLevel())) return; // skip extreme VIX days

        // Collect candidates sorted by predScore desc
        List<SymbolState> candidates = stateRegistry.all().stream()
            .filter(s -> s.getTotalVolume() > 0 && s.getLtp() > 0)
            .filter(s -> !openPositions.containsKey(s.getSymbol()))
            .filter(s -> !tradedToday.contains(s.getSymbol()))
            .toList();

        int entriesThisMinute = 0;
        int MAX_ENTRIES_PER_MINUTE = 3; // max 3 new trades per minute to avoid overtrading

        for (SymbolState state : candidates) {
            if (entriesThisMinute >= MAX_ENTRIES_PER_MINUTE) break;

            var pred = predEngine.score(state);
            double predScore  = pred.predictionScore();
            double deltaStr   = state.getDeltaStrength();
            double histProb   = state.getHistoricalUpProb();
            double mlProb     = state.getMlUpProb();
            boolean absorption = state.isAbsorption();

            // Entry conditions
            boolean qualifies = predScore >= MIN_PRED_SCORE
                && Math.abs(deltaStr) >= MIN_DELTA_STR
                && (histProb >= MIN_HIST_PROB || mlProb >= MIN_ML_PROB)
                && !absorption
                && "BULLISH".equals(pred.signal());

            if (qualifies) {
                enterTrade(state, pred, deltaStr, histProb, mlProb, now, today);
                entriesThisMinute++;
            }
        }
    }

    private void enterTrade(SymbolState state, com.nse.ingest.dto.PredictionResultDto pred,
                             double deltaStr, double histProb, double mlProb,
                             ZonedDateTime now, LocalDate today) {
        double entryPrice = state.getLtp();
        String symbol     = state.getSymbol();
        var regime        = MarketScheduler.REGIME;

        try {
            Long id = jdbc.queryForObject("""
                INSERT INTO delta_paper_trades
                  (trade_date, symbol, signal, entry_time, entry_price, qty, status,
                   pred_score, hist_prob, ml_prob, delta_strength, expected_move,
                   vix_level, is_expiry_day)
                VALUES (?, ?, 'LONG', ?, ?, ?, 'OPEN',
                        ?, ?, ?, ?, ?,
                        ?, ?)
                RETURNING id
                """,
                Long.class,
                java.sql.Date.valueOf(today),
                symbol,
                java.sql.Timestamp.from(now.toInstant()),
                entryPrice, QTY,
                pred.predictionScore(),
                histProb, mlProb * 100,
                deltaStr, state.getExpectedMove(),
                regime.getVixLevel(), regime.isExpiryDay()
            );

            openPositions.put(symbol, id);
            entryTimes.put(symbol, now.toInstant().toEpochMilli());
            entryPrices.put(symbol, entryPrice);
            tradedToday.add(symbol);

            log.info("[PAPER] ENTRY {} @ ₹{} score={} dStr={}% histP={}% mlP={}%",
                     symbol, entryPrice, Math.round(pred.predictionScore()),
                     String.format("%.1f", deltaStr),
                     String.format("%.1f", histProb),
                     String.format("%.0f", mlProb * 100));
        } catch (Exception e) {
            log.warn("[PAPER] Entry insert failed for {}: {}", symbol, e.getMessage());
        }
    }

    private void closeTrade(long id, String symbol, double exitPrice,
                             ZonedDateTime now, String reason) {
        double entryPrice = entryPrices.getOrDefault(symbol, exitPrice);
        double pnl = (exitPrice - entryPrice) * QTY;

        try {
            jdbc.update("""
                UPDATE delta_paper_trades
                SET exit_time=?, exit_price=?, pnl=?, status='CLOSED', exit_reason=?
                WHERE id=?
                """,
                java.sql.Timestamp.from(now.toInstant()),
                exitPrice, pnl, reason, id
            );
            log.info("[PAPER] EXIT {} @ ₹{} → P&L: ₹{} ({})",
                     symbol, exitPrice, String.format("%.2f", pnl), reason);
        } catch (Exception e) {
            log.warn("[PAPER] Close trade failed for {}: {}", symbol, e.getMessage());
        } finally {
            openPositions.remove(symbol);
            entryTimes.remove(symbol);
            entryPrices.remove(symbol);
        }
    }

    private void closeAllOpenPositions(String reason, ZonedDateTime now) {
        for (String symbol : new ArrayList<>(openPositions.keySet())) {
            SymbolState state = stateRegistry.get(symbol);
            double exitPrice  = (state != null && state.getLtp() > 0)
                ? state.getLtp() : entryPrices.getOrDefault(symbol, 0.0);
            closeTrade(openPositions.get(symbol), symbol, exitPrice, now, reason);
        }
    }

    public int openCount() { return openPositions.size(); }
}
