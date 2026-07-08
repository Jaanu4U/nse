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
 *   - predScore >= 65 (strong BULLISH) + deltaStrength in [2, 300]
 *   - historicalUpProb >= 50% OR mlUpProb >= 0.28
 *   - price > VWAP           (trend confirmation — don't buy weakness)
 *   - cumulativeDelta > 0    (net accumulation, not distribution)
 *   - ATR14 valid            (needed for stop/target/size)
 *   - Market breadth >= 45%  (skip LONGs on a weak tape)
 *   - absorption == false, VIX != EXTREME, no re-entry same symbol
 *   - Time 09:20–10:30 IST, max MAX_TRADES_PER_DAY per day
 *
 * Exit rules (ATR-based bracket, checked every minute):
 *   - STOP_LOSS       : ltp <= entry − 1.5×ATR (hard risk control)
 *   - PARTIAL at +1R  : sell 50% at entry + 1.5×ATR, stop → breakeven
 *   - TRAIL_STOP      : after +1×ATR, stop trails highSinceEntry − 1×ATR
 *   - TARGET          : full exit at entry + 2.5×ATR
 *   - TIME_EXIT       : max hold 60 min
 *   - REVERSAL        : BEARISH score <= 35 AND negative 1-min delta
 *   - EOD             : force close 15:20 IST
 *
 * Sizing: volatility-adjusted — qty = min(RISK_PER_TRADE / (1.5×ATR),
 * CAPITAL_PER_TRADE / entry). Equal ₹ risk per stop-out, capped by notional.
 */
@Service
public class PaperTradingEngine {

    private static final Logger log = LoggerFactory.getLogger(PaperTradingEngine.class);

    private static final double CAPITAL_PER_TRADE = 50_000.0; // ₹ notional cap per position
    private static final double RISK_PER_TRADE    = 500.0;    // ₹ lost if stop is hit
    private static final int    MAX_TRADES_PER_DAY = 30;
    private static final double MIN_PRED_SCORE  = 65.0;
    private static final double MIN_DELTA_STR   = 2.0;
    // 2026-07-08 audit: delta_strength 100–300 was the only profitable bucket;
    // >300 = chasing exhaustion (−₹44k on 37 trades). Cap it.
    private static final double MAX_DELTA_STR   = 300.0;
    // Calibrated to actual data distributions (see PREDICTION_FLOW_AUDIT.md C1):
    // up_prob_overall universe max ≈ 51.9 (mean 46) → 50 = top tail
    // ml_up_prob universe max ≈ 0.34 (mean 0.22, base rate 0.15) → 0.28 = top tail
    private static final double MIN_HIST_PROB   = 50.0;
    private static final double MIN_ML_PROB     = 0.28;
    private static final LocalTime ENTRY_START  = LocalTime.of(9, 20);
    // 2026-07-08 audit: entries 10:30+ bled −₹26.5k on just 12 trades
    private static final LocalTime ENTRY_END    = LocalTime.of(10, 30);
    private static final LocalTime EOD_EXIT     = LocalTime.of(15, 20);
    // ATR bracket multipliers
    private static final double STOP_ATR    = 1.5;  // initial stop distance
    private static final double TARGET_ATR  = 2.5;  // full profit target
    private static final double TRAIL_ATR   = 1.0;  // trail distance + activation
    private static final int    MAX_HOLD_MINS = 60; // hard time exit
    // Regime gate: minimum % of live symbols trading above VWAP for LONG entries
    private static final double MIN_BREADTH_PCT = 45.0;

    /** Live state of one open paper position. */
    private static final class Position {
        final long   id;
        final long   entryMillis;
        final double entryPrice;
        final double atr;               // ATR14 captured at entry
        final int    initialQty;
        volatile int     qty;           // remaining qty (after partial)
        volatile double  stop;          // current stop price (trails up)
        volatile double  highSinceEntry;
        volatile boolean partialDone;
        volatile double  realizedPnl;   // banked by the partial exit

        Position(long id, long entryMillis, double entryPrice, double atr,
                 int qty, double stop) {
            this.id = id; this.entryMillis = entryMillis; this.entryPrice = entryPrice;
            this.atr = atr; this.initialQty = qty; this.qty = qty; this.stop = stop;
            this.highSinceEntry = entryPrice;
        }
    }

    /** symbol → open position */
    private final Map<String, Position> positions = new ConcurrentHashMap<>();
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
     * CRASH RECOVERY: reload today's OPEN trades from the DB into memory.
     * Without this, a container restart orphans open positions — they stay
     * OPEN in the DB forever and are never exited.
     * Runs after the Spring context is ready.
     */
    @jakarta.annotation.PostConstruct
    void recoverOpenPositions() {
        try {
            var rows = jdbc.queryForList("""
                SELECT id, symbol, entry_time, entry_price, qty,
                       atr_entry, stop_price, partial_pnl
                FROM delta_paper_trades
                WHERE status = 'OPEN' AND trade_date = CURRENT_DATE
                """);
            for (var row : rows) {
                String symbol   = (String) row.get("symbol");
                Number id       = (Number) row.get("id");
                java.sql.Timestamp entryTs = (java.sql.Timestamp) row.get("entry_time");
                Number entryPx  = (Number) row.get("entry_price");
                Number qty      = (Number) row.get("qty");
                Number atr      = (Number) row.get("atr_entry");
                Number stopPx   = (Number) row.get("stop_price");
                Number partial  = (Number) row.get("partial_pnl");

                double entry  = entryPx.doubleValue();
                double atrVal = atr  == null ? entry * 0.01 : atr.doubleValue();
                double stop   = stopPx == null ? entry - STOP_ATR * atrVal : stopPx.doubleValue();
                Position pos = new Position(id.longValue(), entryTs.getTime(),
                                             entry, atrVal,
                                             qty == null ? 1 : qty.intValue(), stop);
                if (partial != null && partial.doubleValue() != 0) {
                    pos.partialDone = true;
                    pos.realizedPnl = partial.doubleValue();
                    pos.qty         = Math.max(1, pos.initialQty - pos.initialQty / 2);
                }
                positions.put(symbol, pos);
                tradedToday.add(symbol);
            }
            if (!rows.isEmpty()) {
                log.info("[PAPER] Recovered {} open positions from DB after restart", rows.size());
            }
            // Orphans from PREVIOUS days can never be exited meaningfully — close at entry price
            int stale = jdbc.update("""
                UPDATE delta_paper_trades
                SET status='CLOSED', exit_time=entry_time, exit_price=entry_price,
                    pnl=0, exit_reason='ORPHANED_RESTART'
                WHERE status='OPEN' AND trade_date < CURRENT_DATE
                """);
            if (stale > 0) log.info("[PAPER] Closed {} stale orphaned trades from previous days", stale);
        } catch (Exception e) {
            log.warn("[PAPER] Open-position recovery failed: {}", e.getMessage());
        }
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
            positions.clear();
            tradedToday.clear();
            log.info("[PAPER] New trading day reset for {}", today);
        }

        // Force-close all open positions at EOD
        if (time.isAfter(EOD_EXIT)) {
            closeAllOpenPositions("EOD", now);
            return;
        }

        // Process open positions (ATR bracket: stop / partial / trail / target / time)
        processExits(now);

        // Scan for new entries (only during valid entry window)
        if (time.isAfter(ENTRY_START) && time.isBefore(ENTRY_END)) {
            scanForEntries(now, today);
        }
    }

    private void processExits(ZonedDateTime now) {
        for (var entry : new ArrayList<>(positions.entrySet())) {
            String symbol = entry.getKey();
            Position pos  = entry.getValue();
            SymbolState state = stateRegistry.get(symbol);
            if (state == null || state.getLtp() <= 0) continue;

            double ltp = state.getLtp();
            if (ltp > pos.highSinceEntry) pos.highSinceEntry = ltp;

            double ep  = pos.entryPrice;
            double atr = pos.atr;

            // 1) PARTIAL SCALE-OUT: bank 50% at +1R (= STOP_ATR × ATR above entry),
            //    move stop to breakeven — the rest rides risk-free.
            if (!pos.partialDone && pos.qty >= 2 && ltp >= ep + STOP_ATR * atr) {
                int half = pos.qty / 2;
                double banked = (ltp - ep) * half;
                pos.realizedPnl += banked;
                pos.qty         -= half;
                pos.partialDone  = true;
                pos.stop         = Math.max(pos.stop, ep); // breakeven
                try {
                    jdbc.update("UPDATE delta_paper_trades SET partial_pnl=?, stop_price=? WHERE id=?",
                                pos.realizedPnl, pos.stop, pos.id);
                } catch (Exception e) { /* non-fatal */ }
                log.info("[PAPER] PARTIAL {} sold {} @ ₹{} (+₹{}), stop → breakeven",
                         symbol, half, ltp, String.format("%.2f", banked));
            }

            // 2) TRAILING STOP: once price is +TRAIL_ATR×ATR in profit,
            //    ratchet the stop to highSinceEntry − TRAIL_ATR×ATR.
            if (ltp > ep + TRAIL_ATR * atr) {
                double trail = pos.highSinceEntry - TRAIL_ATR * atr;
                if (trail > pos.stop) pos.stop = trail;
            }

            long minutesSinceEntry = (now.toInstant().toEpochMilli() - pos.entryMillis) / 60_000;
            String exitReason = null;

            if (ltp <= pos.stop) {
                exitReason = pos.stop > ep  ? "TRAIL_STOP"
                           : pos.stop == ep ? "BREAKEVEN_STOP"
                           : "STOP_LOSS";
            } else if (ltp >= ep + TARGET_ATR * atr) {
                exitReason = "TARGET";
            } else if (minutesSinceEntry >= MAX_HOLD_MINS) {
                exitReason = "TIME_EXIT";
            } else {
                // Signal reversal — require BOTH a bearish score AND actual
                // selling pressure (negative 1-min delta). Score-only reversal
                // exits had an 11.8% win rate on 2026-07-08.
                var pred = predEngine.score(state);
                if ("BEARISH".equals(pred.signal()) && pred.predictionScore() <= 35
                        && state.getDeltaEngine().getDeltaRate() < 0) {
                    exitReason = "REVERSAL";
                }
            }

            if (exitReason != null) {
                closeTrade(symbol, pos, ltp, now, exitReason);
            }
        }
    }

    /** % of live symbols trading above their VWAP — cheap intraday breadth gauge. */
    private double marketBreadthPct() {
        int total = 0, above = 0;
        for (SymbolState s : stateRegistry.all()) {
            double ltp = s.getLtp(), vwap = s.getVwap();
            if (ltp <= 0 || vwap <= 0 || s.getTotalVolume() == 0) continue;
            total++;
            if (ltp > vwap) above++;
        }
        return total == 0 ? 100.0 : 100.0 * above / total;
    }

    private void scanForEntries(ZonedDateTime now, LocalDate today) {
        var regime = MarketScheduler.REGIME;
        if ("EXTREME".equals(regime.getVixLevel())) return; // skip extreme VIX days
        if (tradedToday.size() >= MAX_TRADES_PER_DAY) return; // daily cap

        // REGIME GATE: skip LONG entries on a weak tape (few stocks above VWAP)
        double breadth = marketBreadthPct();
        if (breadth < MIN_BREADTH_PCT) {
            log.debug("[PAPER] Breadth {}% < {}% — no LONG entries this minute",
                      String.format("%.0f", breadth), (int) MIN_BREADTH_PCT);
            return;
        }

        // Collect candidates sorted by predScore desc
        List<SymbolState> candidates = stateRegistry.all().stream()
            .filter(s -> s.getTotalVolume() > 0 && s.getLtp() > 0)
            .filter(s -> !positions.containsKey(s.getSymbol()))
            .filter(s -> !tradedToday.contains(s.getSymbol()))
            .toList();

        int entriesThisMinute = 0;
        int MAX_ENTRIES_PER_MINUTE = 3; // max 3 new trades per minute to avoid overtrading

        for (SymbolState state : candidates) {
            if (entriesThisMinute >= MAX_ENTRIES_PER_MINUTE) break;
            if (tradedToday.size() >= MAX_TRADES_PER_DAY) break;

            var pred = predEngine.score(state);
            double predScore  = pred.predictionScore();
            double deltaStr   = state.getDeltaStrength();
            double histProb   = state.getHistoricalUpProb();
            double mlProb     = state.getMlUpProb();
            boolean absorption = state.isAbsorption();
            double ltp        = state.getLtp();
            double vwap       = state.getVwap();
            double atr        = state.getIndicators().getAtr14();
            long   cumDelta   = state.getDeltaEngine().getCumulativeDelta();

            // Entry conditions:
            //   score/prob stack + delta band (pressure but not exhaustion)
            //   + price above VWAP (don't buy weakness)
            //   + positive cumulative delta (accumulation, not distribution)
            //   + valid ATR (required for the stop/target bracket)
            boolean qualifies = predScore >= MIN_PRED_SCORE
                && Math.abs(deltaStr) >= MIN_DELTA_STR
                && Math.abs(deltaStr) <= MAX_DELTA_STR
                && (histProb >= MIN_HIST_PROB || mlProb >= MIN_ML_PROB)
                && !absorption
                && "BULLISH".equals(pred.signal())
                && vwap > 0 && ltp > vwap
                && cumDelta > 0
                && !Double.isNaN(atr) && atr > 0;

            if (qualifies) {
                enterTrade(state, pred, deltaStr, histProb, mlProb, atr, now, today);
                entriesThisMinute++;
            }
        }
    }

    private void enterTrade(SymbolState state, com.nse.ingest.dto.PredictionResultDto pred,
                             double deltaStr, double histProb, double mlProb, double atr,
                             ZonedDateTime now, LocalDate today) {
        double entryPrice = state.getLtp();
        String symbol     = state.getSymbol();
        var regime        = MarketScheduler.REGIME;

        // VOLATILITY-ADJUSTED SIZING: equal ₹ risk per stop-out,
        // capped by max notional. riskPerShare = initial stop distance.
        double riskPerShare = STOP_ATR * atr;
        int qtyByRisk     = (int) Math.floor(RISK_PER_TRADE / riskPerShare);
        int qtyByNotional = (int) Math.floor(CAPITAL_PER_TRADE / entryPrice);
        int qty = Math.max(1, Math.min(qtyByRisk, qtyByNotional));

        double stop = entryPrice - STOP_ATR * atr;

        try {
            Long id = jdbc.queryForObject("""
                INSERT INTO delta_paper_trades
                  (trade_date, symbol, signal, entry_time, entry_price, qty, status,
                   pred_score, hist_prob, ml_prob, delta_strength, expected_move,
                   vix_level, is_expiry_day, atr_entry, stop_price)
                VALUES (?, ?, 'LONG', ?, ?, ?, 'OPEN',
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?)
                RETURNING id
                """,
                Long.class,
                java.sql.Date.valueOf(today),
                symbol,
                java.sql.Timestamp.from(now.toInstant()),
                entryPrice, qty,
                pred.predictionScore(),
                histProb, mlProb * 100,
                deltaStr, state.getExpectedMove(),
                regime.getVixLevel(), regime.isExpiryDay(),
                atr, stop
            );

            positions.put(symbol, new Position(id, now.toInstant().toEpochMilli(),
                                                entryPrice, atr, qty, stop));
            tradedToday.add(symbol);

            log.info("[PAPER] ENTRY {} @ ₹{} qty={} stop=₹{} target=₹{} score={} dStr={}% atr={}",
                     symbol, entryPrice, qty,
                     String.format("%.2f", stop),
                     String.format("%.2f", entryPrice + TARGET_ATR * atr),
                     Math.round(pred.predictionScore()),
                     String.format("%.1f", deltaStr),
                     String.format("%.2f", atr));
        } catch (Exception e) {
            log.warn("[PAPER] Entry insert failed for {}: {}", symbol, e.getMessage());
        }
    }

    private void closeTrade(String symbol, Position pos, double exitPrice,
                             ZonedDateTime now, String reason) {
        // Final P&L = banked partial + remaining qty at exit price
        double pnl = pos.realizedPnl + (exitPrice - pos.entryPrice) * pos.qty;

        try {
            jdbc.update("""
                UPDATE delta_paper_trades
                SET exit_time=?, exit_price=?, pnl=?, status='CLOSED', exit_reason=?,
                    stop_price=?, partial_pnl=?
                WHERE id=?
                """,
                java.sql.Timestamp.from(now.toInstant()),
                exitPrice, pnl, reason,
                pos.stop, pos.realizedPnl == 0 ? null : pos.realizedPnl,
                pos.id
            );
            log.info("[PAPER] EXIT {} @ ₹{} → P&L: ₹{} ({}{})",
                     symbol, exitPrice, String.format("%.2f", pnl), reason,
                     pos.partialDone ? ", incl. partial ₹" + String.format("%.2f", pos.realizedPnl) : "");
        } catch (Exception e) {
            log.warn("[PAPER] Close trade failed for {}: {}", symbol, e.getMessage());
        } finally {
            positions.remove(symbol);
        }
    }

    private void closeAllOpenPositions(String reason, ZonedDateTime now) {
        for (var entry : new ArrayList<>(positions.entrySet())) {
            String symbol = entry.getKey();
            Position pos  = entry.getValue();
            SymbolState state = stateRegistry.get(symbol);
            double exitPrice  = (state != null && state.getLtp() > 0)
                ? state.getLtp() : pos.entryPrice;
            closeTrade(symbol, pos, exitPrice, now, reason);
        }
    }

    public int openCount() { return positions.size(); }
}
