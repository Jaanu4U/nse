package com.nse.ingest.api;

import com.nse.ingest.service.PaperTradingEngine;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * REST API for paper trading history.
 *
 * GET /api/paper/trades?date=2026-07-07      — trades for a specific date (default today)
 * GET /api/paper/trades/daily                 — day-wise P&L summary (last 30 days)
 * GET /api/paper/trades/stats                 — overall stats (win rate, total P&L, etc.)
 */
@RestController
@RequestMapping("/api/paper")
public class PaperTradeController {

    private final JdbcTemplate       jdbc;
    private final PaperTradingEngine engine;

    public PaperTradeController(JdbcTemplate jdbc, PaperTradingEngine engine) {
        this.jdbc   = jdbc;
        this.engine = engine;
    }

    /** All trades for a given date (default today) */
    @GetMapping("/trades")
    public ResponseEntity<List<Map<String, Object>>> trades(
            @RequestParam(defaultValue = "today") String date) {

        String sql = """
            SELECT id, trade_date, symbol, signal, status,
                    TO_CHAR(entry_time AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD HH24:MI:SS') AS entry_ist,
                   entry_price,
                    CASE WHEN exit_time IS NULL THEN NULL
                        ELSE TO_CHAR(exit_time AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD HH24:MI:SS')
                    END AS exit_ist,
                   exit_price, qty,
                   ROUND(pnl, 2) AS pnl,
                   exit_reason,
                   ROUND(pred_score, 1) AS pred_score,
                   ROUND(hist_prob, 1) AS hist_prob,
                   ROUND(ml_prob, 1) AS ml_prob,
                   ROUND(delta_strength, 2) AS delta_strength,
                   ROUND(expected_move, 3) AS expected_move,
                   vix_level, is_expiry_day,
                   ROUND(atr_entry, 2) AS atr_entry,
                   ROUND(stop_price, 2) AS stop_price,
                   ROUND(partial_pnl, 2) AS partial_pnl
            FROM delta_paper_trades
            WHERE trade_date = %s
            ORDER BY entry_time
            """;

        String dateSql = "today".equals(date)
            ? sql.formatted("CURRENT_DATE")
            : sql.formatted("'" + date + "'::date");

        List<Map<String, Object>> rows = jdbc.queryForList(dateSql);
        return ResponseEntity.ok(rows);
    }

    /** Day-wise P&L summary for last 30 trading days */
    @GetMapping("/trades/daily")
    public ResponseEntity<List<Map<String, Object>>> daily() {
        String sql = """
            SELECT
                trade_date,
                COUNT(*) AS total_trades,
                SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN pnl = 0 THEN 1 ELSE 0 END) AS breakeven,
                ROUND(SUM(COALESCE(pnl, 0)), 2) AS total_pnl,
                ROUND(AVG(CASE WHEN pnl IS NOT NULL THEN pnl END), 2) AS avg_pnl,
                ROUND(MAX(pnl), 2) AS best_trade,
                ROUND(MIN(pnl), 2) AS worst_trade,
                ROUND(
                    CASE WHEN COUNT(CASE WHEN pnl IS NOT NULL THEN 1 END) > 0
                    THEN 100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)
                              / COUNT(CASE WHEN pnl IS NOT NULL THEN 1 END)
                    ELSE 0 END, 1
                ) AS win_rate_pct
            FROM delta_paper_trades
            WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY trade_date
            ORDER BY trade_date DESC
            """;
        return ResponseEntity.ok(jdbc.queryForList(sql));
    }

    /** Overall stats */
    @GetMapping("/trades/stats")
    public ResponseEntity<Map<String, Object>> stats() {
        String sql = """
            SELECT
                COUNT(*) AS total_trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS total_wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS total_losses,
                ROUND(SUM(COALESCE(pnl, 0)), 2) AS total_pnl,
                ROUND(AVG(CASE WHEN pnl IS NOT NULL THEN pnl END), 2) AS avg_pnl_per_trade,
                ROUND(MAX(pnl), 2) AS best_trade,
                ROUND(MIN(pnl), 2) AS worst_trade,
                ROUND(
                    CASE WHEN COUNT(CASE WHEN pnl IS NOT NULL THEN 1 END) > 0
                    THEN 100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)
                              / COUNT(CASE WHEN pnl IS NOT NULL THEN 1 END)
                    ELSE 0 END, 1
                ) AS win_rate_pct,
                COUNT(DISTINCT trade_date) AS trading_days,
                SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_positions
            FROM delta_paper_trades
            """;
        var rows = jdbc.queryForList(sql);
        Map<String, Object> result = rows.isEmpty() ? Map.of() : rows.get(0);
        // Add live open count from engine
        return ResponseEntity.ok(
            new java.util.LinkedHashMap<>(result) {{ put("live_open", engine.openCount()); }}
        );
    }
}
