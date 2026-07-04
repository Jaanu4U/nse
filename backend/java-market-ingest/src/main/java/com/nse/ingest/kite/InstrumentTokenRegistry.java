package com.nse.ingest.kite;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Downloads the NSE instrument list from Kite REST API and provides
 * instrument_token → trading_symbol resolution used by the packet parser.
 */
@Component
public class InstrumentTokenRegistry {

    private static final Logger log = LoggerFactory.getLogger(InstrumentTokenRegistry.class);
    private static final String INSTRUMENTS_URL = "https://api.kite.trade/instruments/NSE";

    private final Map<Integer, String> tokenToSymbol = new ConcurrentHashMap<>(500);
    private final Map<String, Integer> symbolToToken = new ConcurrentHashMap<>(500);
    private final ObjectMapper mapper = new ObjectMapper();

    /** Pre-seed well-known tokens for a fast start; full list loaded on connect. */
    public void loadFromKite(String accessToken, String apiKey) {
        try {
            HttpClient client = HttpClient.newHttpClient();
            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(INSTRUMENTS_URL))
                .header("X-Kite-Version", "3")
                .header("Authorization", "token " + apiKey + ":" + accessToken)
                .GET().build();
            HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() == 200) {
                parseCsv(resp.body());
                log.info("Loaded {} NSE instruments from Kite", tokenToSymbol.size());
            } else {
                log.error("Failed to fetch instruments: HTTP {}", resp.statusCode());
            }
        } catch (Exception e) {
            log.error("Instrument registry load failed", e);
        }
    }

    private void parseCsv(String csv) {
        String[] lines = csv.split("\n");
        if (lines.length < 2) return;
        // Header: instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,...
        for (int i = 1; i < lines.length; i++) {
            String[] cols = lines[i].split(",");
            if (cols.length < 3) continue;
            try {
                int    token  = Integer.parseInt(cols[0].trim());
                String symbol = cols[2].trim();
                tokenToSymbol.put(token, symbol);
                symbolToToken.put(symbol, token);
            } catch (NumberFormatException ignored) {}
        }
    }

    /** Register a single token→symbol mapping (for testing or manual seeds). */
    public void register(int token, String symbol) {
        tokenToSymbol.put(token, symbol);
        symbolToToken.put(symbol, token);
    }

    public String resolve(int token) {
        return tokenToSymbol.get(token);
    }

    public Integer token(String symbol) {
        return symbolToToken.get(symbol);
    }

    public Map<String, Integer> getAllSymbols() {
        return java.util.Collections.unmodifiableMap(symbolToToken);
    }
}
