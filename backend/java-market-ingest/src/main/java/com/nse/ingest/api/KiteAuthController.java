package com.nse.ingest.api;

import com.nse.ingest.kite.KiteAuthService;
import com.nse.ingest.kite.InstrumentTokenRegistry;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/kite")
public class KiteAuthController {

    private final KiteAuthService       auth;
    private final InstrumentTokenRegistry registry;

    public KiteAuthController(KiteAuthService auth, InstrumentTokenRegistry registry) {
        this.auth     = auth;
        this.registry = registry;
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
            registry.loadFromKite(token, "xcnose6lzs8vuzik");
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
        registry.loadFromKite(token, "xcnose6lzs8vuzik");
        return ResponseEntity.ok(Map.of("message", "Token set successfully"));
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
