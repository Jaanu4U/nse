package com.nse.ingest.kite;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.nse.ingest.config.KiteProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;

/**
 * Fetches historical daily OHLCV bars from Kite Connect REST API.
 *
 * Kite historical endpoint:
 *   GET /instruments/historical/{instrument_token}/day
 *   ?from=YYYY-MM-DD&to=YYYY-MM-DD&continuous=0&oi=0
 *
 * Rate limit: Kite allows 3 requests/second for historical data.
 * This service enforces that via a token-bucket semaphore.
 */
@Service
public class KiteHistoricalService {

    private static final Logger log = LoggerFactory.getLogger(KiteHistoricalService.class);
    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd");
    private static final int MAX_RPS = 3; // Kite historical rate limit

    private final KiteProperties          props;
    private final KiteAuthService         auth;
    private final InstrumentTokenRegistry registry;
    private final HttpClient              http   = HttpClient.newHttpClient();
    private final ObjectMapper            mapper = new ObjectMapper();
    // Token bucket: MAX_RPS permits, refill every second
    private final Semaphore               bucket = new Semaphore(MAX_RPS, true);

    public KiteHistoricalService(KiteProperties props,
                                  KiteAuthService auth,
                                  InstrumentTokenRegistry registry) {
        this.props    = props;
        this.auth     = auth;
        this.registry = registry;
        startRefiller();
    }

    public record OhlcvBar(LocalDate date, double open, double high,
                           double low, double close, long volume) {}

    /**
     * Fetch daily bars for a symbol between from and to dates.
     * Returns an empty list if the token is not found or the API fails.
     */
    public List<OhlcvBar> fetchDaily(String symbol, LocalDate from, LocalDate to) {
        Integer token = registry.token(symbol);
        if (token == null) {
            log.debug("No token for {}", symbol);
            return List.of();
        }
        try {
            bucket.acquire(); // rate-limit
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return List.of();
        }
        String url = String.format(
            "%s/instruments/historical/%d/day?from=%s&to=%s&continuous=0&oi=0",
            props.getBaseUrl(), token,
            from.format(FMT), to.format(FMT)
        );
        try {
            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .header("X-Kite-Version", "3")
                .header("Authorization", "token " + props.getApiKey() + ":" + auth.getAccessToken())
                .GET().build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() == 429) {
                log.warn("Rate limited for {}; backing off 2s", symbol);
                Thread.sleep(2000);
                return fetchDaily(symbol, from, to); // retry once
            }
            if (resp.statusCode() != 200) {
                log.warn("Historical fetch {} HTTP {}: {}", symbol, resp.statusCode(),
                    resp.body().length() > 200 ? resp.body().substring(0, 200) : resp.body());
                return List.of();
            }
            return parseCandles(mapper.readTree(resp.body()));
        } catch (Exception e) {
            log.error("Historical fetch failed for {}: {}", symbol, e.getMessage());
            return List.of();
        }
    }

    private List<OhlcvBar> parseCandles(JsonNode root) {
        List<OhlcvBar> bars = new ArrayList<>();
        JsonNode candles = root.path("data").path("candles");
        if (!candles.isArray()) return bars;
        for (JsonNode c : candles) {
            if (!c.isArray() || c.size() < 6) continue;
            // Format: [datetime, open, high, low, close, volume, oi?]
            LocalDate date = LocalDate.parse(c.get(0).asText().substring(0, 10));
            bars.add(new OhlcvBar(
                date,
                c.get(1).asDouble(),   // open
                c.get(2).asDouble(),   // high
                c.get(3).asDouble(),   // low
                c.get(4).asDouble(),   // close
                c.get(5).asLong()      // volume
            ));
        }
        return bars;
    }

    // Token bucket refiller — adds MAX_RPS permits every second
    private void startRefiller() {
        Thread.ofVirtual().start(() -> {
            while (!Thread.currentThread().isInterrupted()) {
                try {
                    TimeUnit.SECONDS.sleep(1);
                    int deficit = MAX_RPS - bucket.availablePermits();
                    if (deficit > 0) bucket.release(deficit);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
        });
    }
}
