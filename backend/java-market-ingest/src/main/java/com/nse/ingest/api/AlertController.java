package com.nse.ingest.api;

import com.nse.ingest.domain.AlertFired;
import com.nse.ingest.dto.AlertRuleRequest;
import com.nse.ingest.service.AlertService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/alerts")
public class AlertController {

    private final AlertService service;

    public AlertController(AlertService service) {
        this.service = service;
    }

    /** GET /api/alerts/rules — list all live in-memory rules */
    @GetMapping("/rules")
    public ResponseEntity<List<?>> rules() {
        return ResponseEntity.ok(service.liveRules());
    }

    /** POST /api/alerts/rules — create a new alert rule */
    @PostMapping("/rules")
    public ResponseEntity<?> create(@RequestBody AlertRuleRequest req) {
        try {
            return ResponseEntity.ok(service.createRule(req));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("error", "Unknown alert type: " + req.type() +
                ". Valid types: PRICE_ABOVE, PRICE_BELOW, DELTA_SPIKE_BUY, DELTA_SPIKE_SELL, VOLUME_SPIKE, " +
                "RSI_OVERBOUGHT, RSI_OVERSOLD, MACD_CROSSOVER_BULLISH, MACD_CROSSOVER_BEARISH, " +
                "BB_SQUEEZE, BB_BREAKOUT_UP, BB_BREAKOUT_DOWN, SUPERTREND_BUY, SUPERTREND_SELL"));
        }
    }

    /** DELETE /api/alerts/rules/{id} — deactivate a rule */
    @DeleteMapping("/rules/{id}")
    public ResponseEntity<Map<String, String>> delete(@PathVariable long id) {
        service.deleteRule(id);
        return ResponseEntity.ok(Map.of("message", "Rule " + id + " deactivated"));
    }

    /** GET /api/alerts/fired?hours=2 — recent alerts fired */
    @GetMapping("/fired")
    public ResponseEntity<List<AlertFired>> fired(@RequestParam(defaultValue = "2") int hours) {
        return ResponseEntity.ok(service.recentFired(hours));
    }
}
