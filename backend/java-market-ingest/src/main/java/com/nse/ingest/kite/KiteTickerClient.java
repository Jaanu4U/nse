package com.nse.ingest.kite;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.nse.ingest.config.KiteProperties;
import com.nse.ingest.dto.TickDto;
import com.nse.ingest.service.TickProcessorService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * Production Kite Connect WebSocket ticker client.
 *
 * Features:
 *  - Connects to wss://ws.kite.trade with api_key and access_token.
 *  - Subscribes to up to 400 instruments in "full" mode (OHLC + depth).
 *  - Sends heartbeat pings every 25 seconds (Kite disconnects after 60s silence).
 *  - Auto-reconnects with exponential back-off on any error or close.
 *  - Binary frame reassembly: accumulates partial frames before parsing.
 *  - Dispatches parsed TickDto objects to TickProcessorService via virtual threads.
 */
@Component
public class KiteTickerClient {

    private static final Logger log = LoggerFactory.getLogger(KiteTickerClient.class);

    private final KiteProperties      props;
    private final KiteAuthService     auth;
    private final InstrumentTokenRegistry registry;
    private final KitePacketParser    parser;
    private final TickProcessorService processor;
    private final ObjectMapper        mapper = new ObjectMapper();

    private volatile WebSocket        ws;
    private final    AtomicBoolean    running    = new AtomicBoolean(false);
    private final    AtomicBoolean    connected  = new AtomicBoolean(false);
    private final    ScheduledExecutorService heartbeatExecutor =
            Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "kite-heartbeat");
                t.setDaemon(true);
                return t;
            });
    private ScheduledFuture<?> heartbeatFuture;
    private final    ByteBuffer frameAccumulator = ByteBuffer.allocate(65536);
    private final    List<Integer> subscribedTokens = new CopyOnWriteArrayList<>();

    public KiteTickerClient(KiteProperties props,
                             KiteAuthService auth,
                             InstrumentTokenRegistry registry,
                             TickProcessorService processor) {
        this.props     = props;
        this.auth      = auth;
        this.registry  = registry;
        this.parser    = new KitePacketParser(registry);
        this.processor = processor;
    }

    // ---- Public lifecycle -----------------------------------------------

    public void connect() {
        if (!auth.isAuthenticated()) {
            log.warn("Cannot connect: no Kite access token. Complete OAuth flow first.");
            return;
        }
        running.set(true);
        doConnect(1);
    }

    public void disconnect() {
        running.set(false);
        stopHeartbeat();
        WebSocket local = ws;
        if (local != null) {
            local.sendClose(WebSocket.NORMAL_CLOSURE, "shutdown");
        }
    }

    /** Subscribe a list of instruments in "full" mode. */
    public void subscribe(Collection<Integer> tokens) {
        subscribedTokens.clear();
        subscribedTokens.addAll(tokens);
        if (connected.get()) {
            sendSubscription(tokens);
        }
    }

    public boolean isConnected() { return connected.get(); }

    // ---- Private internals ----------------------------------------------

    private void doConnect(int attempt) {
        if (!running.get()) return;
        long delay = Math.min(30_000L, props.getReconnectIntervalMs() * attempt);
        if (attempt > 1) {
            log.info("Reconnecting in {}ms (attempt {})", delay, attempt);
            try { Thread.sleep(delay); } catch (InterruptedException ie) { Thread.currentThread().interrupt(); }
        }
        try {
            String url = props.getWsUrl() + "?api_key=" + props.getApiKey()
                       + "&access_token=" + auth.getAccessToken();
            HttpClient client = HttpClient.newHttpClient();
            int capturedAttempt = attempt;
            ws = client.newWebSocketBuilder()
                .buildAsync(URI.create(url), new WebSocket.Listener() {

                    @Override
                    public void onOpen(WebSocket webSocket) {
                        connected.set(true);
                        log.info("Kite WebSocket connected (attempt {})", capturedAttempt);
                        frameAccumulator.clear();
                        startHeartbeat();
                        if (!subscribedTokens.isEmpty()) sendSubscription(subscribedTokens);
                        webSocket.request(Long.MAX_VALUE);
                    }

                    @Override
                    public CompletionStage<?> onBinary(WebSocket webSocket, ByteBuffer data, boolean last) {
                        // Accumulate partial frames
                        frameAccumulator.put(data);
                        if (last) {
                            frameAccumulator.flip();
                            byte[] bytes = new byte[frameAccumulator.limit()];
                            frameAccumulator.get(bytes);
                            frameAccumulator.clear();
                            dispatchTicks(bytes);
                        }
                        return null;
                    }

                    @Override
                    public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
                        // Kite sends text frames for heartbeat confirmations ("{}") – no action needed
                        return null;
                    }

                    @Override
                    public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
                        connected.set(false);
                        stopHeartbeat();
                        log.warn("Kite WebSocket closed: {} – {}", statusCode, reason);
                        if (running.get()) {
                            Thread.ofVirtual().start(() -> doConnect(capturedAttempt + 1));
                        }
                        return null;
                    }

                    @Override
                    public void onError(WebSocket webSocket, Throwable error) {
                        connected.set(false);
                        stopHeartbeat();
                        log.error("Kite WebSocket error", error);
                        if (running.get()) {
                            Thread.ofVirtual().start(() -> doConnect(capturedAttempt + 1));
                        }
                    }
                })
                .join();
        } catch (Exception e) {
            log.error("WebSocket build failed", e);
            if (running.get()) {
                int next = attempt + 1;
                Thread.ofVirtual().start(() -> doConnect(next));
            }
        }
    }

    private void sendSubscription(Collection<Integer> tokens) {
        try {
            // {"a":"subscribe","v":[...tokens...]}
            Map<String, Object> subMsg = Map.of("a", "subscribe", "v", new ArrayList<>(tokens));
            ws.sendText(mapper.writeValueAsString(subMsg), true);
            // Set mode to full
            Map<String, Object> modeMsg = Map.of("a", "mode", "v", List.of("full", new ArrayList<>(tokens)));
            ws.sendText(mapper.writeValueAsString(modeMsg), true);
            log.info("Subscribed to {} instruments in full mode", tokens.size());
        } catch (Exception e) {
            log.error("Subscription send failed", e);
        }
    }

    private void dispatchTicks(byte[] data) {
        List<TickDto> ticks = parser.parse(data);
        for (TickDto tick : ticks) {
            Thread.ofVirtual().start(() -> processor.process(tick));
        }
    }

    private void startHeartbeat() {
        stopHeartbeat();
        heartbeatFuture = heartbeatExecutor.scheduleAtFixedRate(() -> {
            try {
                if (ws != null && connected.get()) {
                    ws.sendPing(ByteBuffer.allocate(0));
                }
            } catch (Exception e) {
                log.warn("Heartbeat failed", e);
            }
        }, props.getHeartbeatIntervalMs(), props.getHeartbeatIntervalMs(), TimeUnit.MILLISECONDS);
    }

    private void stopHeartbeat() {
        if (heartbeatFuture != null) {
            heartbeatFuture.cancel(false);
            heartbeatFuture = null;
        }
    }
}
