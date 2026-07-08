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
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
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

    public record MinuteBar(LocalDateTime dateTime, double open, double high,
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
            return parseDailyCandles(mapper.readTree(resp.body()));
        } catch (Exception e) {
            log.error("Historical fetch failed for {}: {}", symbol, e.getMessage());
            return List.of();
        }
    }

    // ---- Minute data -------------------------------------------------------

    /**
     * Fetch 1-minute OHLCV bars for a symbol between two IST datetimes.
     * Used to fill gaps when the system was down during market hours.
     * Note: Kite historical minute data has no buy/sell split.
     */
    public List<MinuteBar> fetchMinute(String symbol, LocalDateTime fromIst, LocalDateTime toIst) {
        Integer token = registry.token(symbol);
        if (token == null) return List.of();
        try {
            bucket.acquire();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return List.of();
        }
        // Kite expects datetime URL-encoded (space → +)
        String fromEnc = URLEncoder.encode(fromIst.format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")), StandardCharsets.UTF_8);
        String toEnc   = URLEncoder.encode(toIst.format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")),   StandardCharsets.UTF_8);
        String url = String.format(
            "%s/instruments/historical/%d/minute?from=%s&to=%s&continuous=0&oi=0",
            props.getBaseUrl(), token, fromEnc, toEnc
        );
        try {
            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .header("X-Kite-Version", "3")
                .header("Authorization", "token " + props.getApiKey() + ":" + auth.getAccessToken())
                .GET().build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() == 429) {
                log.warn("Rate limited (minute) for {}; backing off 2s", symbol);
                Thread.sleep(2000);
                return fetchMinute(symbol, fromIst, toIst);
            }
            if (resp.statusCode() != 200) {
                log.warn("Minute fetch {} HTTP {}: {}", symbol, resp.statusCode(),
                    resp.body().length() > 200 ? resp.body().substring(0, 200) : resp.body());
                return List.of();
            }
            return parseMinuteCandles(mapper.readTree(resp.body()));
        } catch (Exception e) {
            log.error("Minute fetch failed for {}: {}", symbol, e.getMessage());
            return List.of();
        }
    }

    // ---- Parsers ----------------------------------------------------------

    private List<OhlcvBar> parseDailyCandles(JsonNode root) {
        List<OhlcvBar> bars = new ArrayList<>();
        JsonNode candles = root.path("data").path("candles");
        if (!candles.isArray()) return bars;
        for (JsonNode c : candles) {
            if (!c.isArray() || c.size() < 6) continue;
            LocalDate date = LocalDate.parse(c.get(0).asText().substring(0, 10));
            bars.add(new OhlcvBar(date,
                c.get(1).asDouble(), c.get(2).asDouble(),
                c.get(3).asDouble(), c.get(4).asDouble(), c.get(5).asLong()));
        }
        return bars;
    }

    private List<MinuteBar> parseMinuteCandles(JsonNode root) {
        List<MinuteBar> bars = new ArrayList<>();
        JsonNode candles = root.path("data").path("candles");
        if (!candles.isArray()) return bars;
        // Kite minute timestamp: "2026-07-07T09:15:00+0530"
        DateTimeFormatter kiteTs = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssZ");
        ZoneId ist = ZoneId.of("Asia/Kolkata");
        for (JsonNode c : candles) {
            if (!c.isArray() || c.size() < 6) continue;
            java.time.ZonedDateTime zdt = java.time.ZonedDateTime.parse(c.get(0).asText(), kiteTs);
            LocalDateTime dtIst = zdt.withZoneSameInstant(ist).toLocalDateTime();
            bars.add(new MinuteBar(dtIst,
                c.get(1).asDouble(), c.get(2).asDouble(),
                c.get(3).asDouble(), c.get(4).asDouble(), c.get(5).asLong()));
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

