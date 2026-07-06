package com.nse.ingest.api;

import com.nse.ingest.config.KiteProperties;
import com.nse.ingest.kite.KiteAuthService;
import com.nse.ingest.kite.InstrumentTokenRegistry;
import com.nse.ingest.kite.KiteTickerClient;
import com.nse.ingest.kite.WatchlistProvider;
import com.nse.ingest.scheduler.MarketScheduler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/kite")
public class KiteAuthController {

    private static final Logger log = LoggerFactory.getLogger(KiteAuthController.class);

    private final KiteAuthService         auth;
    private final InstrumentTokenRegistry registry;
    private final KiteProperties          props;
    private final KiteTickerClient        ticker;
    private final WatchlistProvider       watchlist;
    private final MarketScheduler         scheduler;

    public KiteAuthController(KiteAuthService auth, InstrumentTokenRegistry registry,
                               KiteProperties props, KiteTickerClient ticker,
                               WatchlistProvider watchlist, MarketScheduler scheduler) {
        this.auth      = auth;
        this.registry  = registry;
        this.props     = props;
        this.ticker    = ticker;
        this.watchlist = watchlist;
        this.scheduler = scheduler;
    }

    /** Step 1: return login URL */
    @GetMapping("/login-url")
    public ResponseEntity<Map<String, String>> loginUrl() {
        return ResponseEntity.ok(Map.of(
            "login_url", auth.loginUrl(),
            "note", "Open this URL in browser to authorize the app"
        ));
    }

    /** Step 2: callback from Kite after user authorizes */
    @GetMapping("/callback")
    public ResponseEntity<Map<String, Object>> callback(
            @RequestParam String request_token,
            @RequestParam(defaultValue = "success") String status) {
        if (!"success".equals(status)) {
            return ResponseEntity.badRequest().body(Map.of("error", "Kite auth failed"));
        }
        try {
            String token = auth.generateSession(request_token);
            // Load instrument registry now that we have a valid token
            registry.loadFromKite(token, props.getApiKey());
            return ResponseEntity.ok(Map.of(
                "message", "Authentication successful",
                "access_token", token,
                "note", "Set KITE_ACCESS_TOKEN=" + token + " in your .env and restart"
            ));
        } catch (Exception e) {
            return ResponseEntity.internalServerError()
                .body(Map.of("error", e.getMessage()));
        }
    }

    /** Step 3: manually supply an already-obtained access token */
    @PostMapping("/access-token")
    public ResponseEntity<Map<String, String>> setToken(@RequestBody Map<String, String> body) {
        String token = body.get("access_token");
        if (token == null || token.isBlank()) {
            return ResponseEntity.badRequest().body(Map.of("error", "access_token is required"));
        }
        auth.setAccessToken(token);
        registry.loadFromKite(token, props.getApiKey());
        // Load 20-day avg volumes so deltaStrength and volumeRatio are accurate
        Thread.ofVirtual().start(scheduler::loadAvgVolumes);
        // Connect WebSocket and subscribe instruments in background
        Thread.ofVirtual().start(() -> {
            try {
                log.info("[AUTH] Connecting WebSocket after token update...");
                ticker.connect();
                Thread.sleep(3000); // wait for connection to establish
                List<String> symbols = watchlist.activeSymbols();
                List<Integer> tokens2 = symbols.stream()
                        .map(registry::token)
                        .filter(java.util.Objects::nonNull)
                        .toList();
                log.info("[AUTH] Subscribing {}/{} instruments via WebSocket", tokens2.size(), symbols.size());
                if (!tokens2.isEmpty()) ticker.subscribe(tokens2);
            } catch (Exception e) {
                log.warn("[AUTH] WebSocket connect/subscribe failed: {}", e.getMessage());
            }
        });
        return ResponseEntity.ok(Map.of("message", "Token set, WebSocket connecting in background"));
    }

    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> status() {
        return ResponseEntity.ok(Map.of(
            "authenticated", auth.isAuthenticated(),
            "instruments_loaded", registry.getAllSymbols().size()
        ));
    }

    @DeleteMapping("/session")
    public ResponseEntity<Map<String, String>> logout() {
        auth.deleteSession();
        return ResponseEntity.ok(Map.of("message", "Session deleted"));
    }
}
