package com.nse.ingest.kite;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.nse.ingest.config.KiteProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Manages Kite Connect authentication lifecycle:
 * 1. Returns the Kite login URL for browser-based OAuth.
 * 2. Exchanges request_token + sha256(api_key+request_token+api_secret) for access_token.
 * 3. Validates access token, refreshes when expired.
 * 4. Exposes the current live access token for use by the ticker client.
 */
@Service
public class KiteAuthService {

    private static final Logger log = LoggerFactory.getLogger(KiteAuthService.class);

    private final KiteProperties props;
    private final ObjectMapper   mapper = new ObjectMapper();
    private final HttpClient     http   = HttpClient.newHttpClient();
    private final AtomicReference<String> liveAccessToken;

    public KiteAuthService(KiteProperties props) {
        this.props          = props;
        this.liveAccessToken = new AtomicReference<>(props.getAccessToken());
    }

    /** @return Kite Connect login URL to redirect the user's browser to. */
    public String loginUrl() {
        return props.getBaseUrl().replace("api.kite.trade", "kite.trade")
               + "/connect/login?api_key=" + props.getApiKey() + "&v=3";
    }

    /**
     * Exchange a one-time request_token for a reusable access_token.
     * Persists the token in memory; caller should also persist to .env / DB.
     *
     * @return access_token string
     */
    public String generateSession(String requestToken) throws Exception {
        String checksum = sha256(props.getApiKey() + requestToken + props.getApiSecret());
        String body = "api_key=" + encode(props.getApiKey())
                    + "&request_token=" + encode(requestToken)
                    + "&checksum=" + encode(checksum);

        HttpRequest req = HttpRequest.newBuilder()
            .uri(URI.create(props.getBaseUrl() + "/session/token"))
            .header("Content-Type", "application/x-www-form-urlencoded")
            .header("X-Kite-Version", "3")
            .POST(HttpRequest.BodyPublishers.ofString(body))
            .build();

        HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
        JsonNode root = mapper.readTree(resp.body());

        if (resp.statusCode() != 200) {
            throw new RuntimeException("Kite session error " + resp.statusCode() + ": " + root.path("message").asText());
        }

        String token = root.path("data").path("access_token").asText();
        if (token == null || token.isBlank()) {
            throw new RuntimeException("access_token missing from Kite response");
        }
        liveAccessToken.set(token);
        log.info("Kite access token refreshed successfully");
        return token;
    }

    /** Invalidate (logout) the current session. */
    public void deleteSession() {
        try {
            String token = liveAccessToken.get();
            if (token == null || token.isBlank()) return;
            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(props.getBaseUrl() + "/session/token?api_key="
                     + props.getApiKey() + "&access_token=" + token))
                .header("X-Kite-Version", "3")
                .DELETE().build();
            http.send(req, HttpResponse.BodyHandlers.discarding());
            liveAccessToken.set(null);
        } catch (Exception e) {
            log.warn("Failed to delete Kite session", e);
        }
    }

    public String getAccessToken()       { return liveAccessToken.get(); }
    public void   setAccessToken(String t) { liveAccessToken.set(t); }
    public boolean isAuthenticated()     { String t = liveAccessToken.get(); return t != null && !t.isBlank(); }

    private static String sha256(String input) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] digest = md.digest(input.getBytes(StandardCharsets.UTF_8));
        return HexFormat.of().formatHex(digest);
    }

    private static String encode(String v) {
        return URLEncoder.encode(v, StandardCharsets.UTF_8);
    }
}
