package com.nse.ingest.kite;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Loads the list of active NSE symbols to watch from the existing
 * {@code stocks} table (shared with the Python backend).
 *
 * This is the single source of truth for WHICH stocks get live delta.
 * All active stocks in the DB automatically get Kite subscriptions.
 *
 * Column layout expected:
 *   stocks (id, symbol, company_name, industry, is_active, ...)
 */
@Component
public class WatchlistProvider {

    private static final Logger log = LoggerFactory.getLogger(WatchlistProvider.class);

    private final JdbcTemplate jdbc;

    public WatchlistProvider(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * Returns symbol → company_name map for all active stocks.
     * Ordered by symbol alphabetically.
     */
    public Map<String, String> loadActiveStocks() {
        try {
            List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT symbol, company_name FROM stocks WHERE is_active = true ORDER BY symbol"
            );
            Map<String, String> result = new LinkedHashMap<>();
            for (Map<String, Object> row : rows) {
                String sym  = (String) row.get("symbol");
                String name = (String) row.getOrDefault("company_name", sym);
                if (sym != null && !sym.isBlank()) {
                    result.put(sym.trim().toUpperCase(), name != null ? name : sym);
                }
            }
            log.info("Loaded {} active stocks from DB for delta tracking", result.size());
            return result;
        } catch (Exception e) {
            log.error("Failed to load active stocks from DB — falling back to empty list", e);
            return Map.of();
        }
    }

    /**
     * Returns only the symbol list (for subscription).
     */
    public List<String> activeSymbols() {
        return List.copyOf(loadActiveStocks().keySet());
    }

    /** Total count of active stocks in the DB. */
    public int count() {
        try {
            Integer n = jdbc.queryForObject(
                "SELECT COUNT(*) FROM stocks WHERE is_active = true", Integer.class);
            return n != null ? n : 0;
        } catch (Exception e) {
            log.warn("Could not count active stocks", e);
            return 0;
        }
    }
}
