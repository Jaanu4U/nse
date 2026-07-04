package com.nse.ingest.engine;

import com.nse.ingest.dto.AlertFiredDto;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.LocalDateTime;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;

/**
 * In-memory alert evaluator.  Each {@link AlertRule} is checked on every
 * qualifying tick.  When a rule fires it emits an {@link AlertFiredDto}
 * to all registered listeners (SSE sink, DB writer, etc.).
 *
 * Rules are stateless by design — they carry the condition and the
 * engine evaluates the current SymbolState snapshot.
 */
public class AlertEngine {

    private static final Logger log = LoggerFactory.getLogger(AlertEngine.class);

    public enum AlertType {
        PRICE_ABOVE, PRICE_BELOW,
        DELTA_SPIKE_BUY, DELTA_SPIKE_SELL,
        VOLUME_SPIKE,
        RSI_OVERBOUGHT, RSI_OVERSOLD,
        MACD_CROSSOVER_BULLISH, MACD_CROSSOVER_BEARISH,
        BB_SQUEEZE, BB_BREAKOUT_UP, BB_BREAKOUT_DOWN,
        SUPERTREND_BUY, SUPERTREND_SELL
    }

    public record AlertRule(
        long   id,
        String symbol,
        AlertType type,
        double threshold,
        boolean once        // fire only once, then self-remove
    ) {}

    private final List<AlertRule>         rules     = new CopyOnWriteArrayList<>();
    private final List<Consumer<AlertFiredDto>> listeners = new CopyOnWriteArrayList<>();
    // Track previously fired once-rules to avoid double fire
    private final java.util.Set<Long>     fired     = java.util.concurrent.ConcurrentHashMap.newKeySet();
    // Track previous MACD histogram sign per symbol for crossover detection
    private final java.util.Map<String, Double> prevMacdHist = new java.util.concurrent.ConcurrentHashMap<>();
    private final java.util.Map<String, Integer> prevStDir    = new java.util.concurrent.ConcurrentHashMap<>();

    public void addRule(AlertRule rule) { rules.add(rule); }
    public void removeRule(long id)     { rules.removeIf(r -> r.id() == id); fired.remove(id); }
    public List<AlertRule> getRules()   { return List.copyOf(rules); }

    public void addListener(Consumer<AlertFiredDto> listener)    { listeners.add(listener); }
    public void removeListener(Consumer<AlertFiredDto> listener) { listeners.remove(listener); }

    /**
     * Called by TickProcessorService after every tick for this symbol.
     * Evaluates all rules matching the symbol and fires any that trip.
     */
    public void evaluate(String symbol, double ltp, double delta, long volume,
                         double rsi, double macdHist, double atr, int stDir,
                         double bbUpper, double bbLower) {

        for (AlertRule r : rules) {
            if (!r.symbol().equals(symbol) && !r.symbol().equals("*")) continue;
            if (r.once() && fired.contains(r.id())) continue;

            boolean trips = switch (r.type()) {
                case PRICE_ABOVE              -> ltp > r.threshold();
                case PRICE_BELOW              -> ltp < r.threshold();
                case DELTA_SPIKE_BUY          -> delta > r.threshold();
                case DELTA_SPIKE_SELL         -> delta < -r.threshold();
                case VOLUME_SPIKE             -> volume > r.threshold();
                case RSI_OVERBOUGHT           -> rsi > r.threshold();
                case RSI_OVERSOLD             -> rsi < r.threshold();
                case MACD_CROSSOVER_BULLISH   -> {
                    double prev = prevMacdHist.getOrDefault(symbol, macdHist);
                    yield prev <= 0 && macdHist > 0;
                }
                case MACD_CROSSOVER_BEARISH   -> {
                    double prev = prevMacdHist.getOrDefault(symbol, macdHist);
                    yield prev >= 0 && macdHist < 0;
                }
                case BB_SQUEEZE               -> !Double.isNaN(bbUpper) && !Double.isNaN(bbLower) && (bbUpper - bbLower) / ltp < r.threshold();
                case BB_BREAKOUT_UP           -> ltp > bbUpper;
                case BB_BREAKOUT_DOWN         -> ltp < bbLower;
                case SUPERTREND_BUY           -> {
                    int prev = prevStDir.getOrDefault(symbol, stDir);
                    yield prev == -1 && stDir == 1;
                }
                case SUPERTREND_SELL          -> {
                    int prev = prevStDir.getOrDefault(symbol, stDir);
                    yield prev == 1 && stDir == -1;
                }
            };

            if (trips) {
                if (r.once()) fired.add(r.id());
                fire(new AlertFiredDto(r.id(), symbol, r.type().name(), ltp, r.threshold(), LocalDateTime.now()));
            }
        }
        // Update previous state for crossover detection
        if (!Double.isNaN(macdHist)) prevMacdHist.put(symbol, macdHist);
        prevStDir.put(symbol, stDir);
    }

    private void fire(AlertFiredDto alert) {
        log.info("ALERT: {} {} {} @ {}", alert.symbol(), alert.type(), alert.value(), alert.firedAt());
        for (Consumer<AlertFiredDto> l : listeners) {
            try { l.accept(alert); } catch (Exception e) { log.warn("Alert listener error", e); }
        }
    }
}
