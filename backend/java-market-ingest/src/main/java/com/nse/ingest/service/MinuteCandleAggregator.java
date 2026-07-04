package com.nse.ingest.service;

import com.nse.ingest.domain.MinuteCandle;
import com.nse.ingest.repo.MinuteCandleRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * Every minute: snapshot each symbol's current OHLCV, build a {@link MinuteCandle},
 * push the bar into the rolling windows, and batch-insert to PostgreSQL.
 *
 * The minute bar is built from the SymbolState's current OHLC which tracks the
 * running bar since the last flush — we do NOT store every tick, only summaries.
 */
@Service
public class MinuteCandleAggregator {

    private static final Logger log = LoggerFactory.getLogger(MinuteCandleAggregator.class);

    private final MarketStateRegistry    registry;
    private final MinuteCandleRepository repo;

    public MinuteCandleAggregator(MarketStateRegistry registry, MinuteCandleRepository repo) {
        this.registry = registry;
        this.repo     = repo;
    }

    /** Runs every minute during market hours (cron managed by MarketScheduler). */
    @Transactional
    public void flushMinuteCandles() {
        LocalDateTime now     = LocalDateTime.now();
        LocalDate     today   = now.toLocalDate();
        List<MinuteCandle> candles = new ArrayList<>(registry.symbolCount());

        for (SymbolState state : registry.all()) {
            if (state.getTotalVolume() == 0) continue;

            MinuteCandle c = new MinuteCandle();
            c.setSymbol(state.getSymbol());
            c.setTradeDate(today);
            c.setMinuteTs(now.withSecond(0).withNano(0));
            c.setOpenPrice(bd(state.getOpen()));
            c.setHighPrice(bd(state.getHigh()));
            c.setLowPrice(bd(state.getLow()));
            c.setClosePrice(bd(state.getLtp()));
            c.setVolume(state.getTotalVolume());
            c.setBuyVolume(state.getDeltaEngine().getBuyVolume());
            c.setSellVolume(state.getDeltaEngine().getSellVolume());
            c.setDelta(state.getDeltaEngine().getDelta());
            candles.add(c);

            // Update rolling windows
            long barDelta = state.getDeltaEngine().snapshotAndResetRate();
            state.window1m.push(now.toEpochSecond(java.time.ZoneOffset.UTC),
                state.getOpen(), state.getHigh(), state.getLow(), state.getLtp(),
                state.getTotalVolume(),
                state.getDeltaEngine().getBuyVolume(),
                state.getDeltaEngine().getSellVolume(),
                barDelta);
            state.window5m.push(now.toEpochSecond(java.time.ZoneOffset.UTC),
                state.getOpen(), state.getHigh(), state.getLow(), state.getLtp(),
                state.getTotalVolume(),
                state.getDeltaEngine().getBuyVolume(),
                state.getDeltaEngine().getSellVolume(),
                barDelta);
            state.window15m.push(now.toEpochSecond(java.time.ZoneOffset.UTC),
                state.getOpen(), state.getHigh(), state.getLow(), state.getLtp(),
                state.getTotalVolume(),
                state.getDeltaEngine().getBuyVolume(),
                state.getDeltaEngine().getSellVolume(),
                barDelta);
            state.windowDay.push(now.toEpochSecond(java.time.ZoneOffset.UTC),
                state.getOpen(), state.getHigh(), state.getLow(), state.getLtp(),
                state.getTotalVolume(),
                state.getDeltaEngine().getBuyVolume(),
                state.getDeltaEngine().getSellVolume(),
                barDelta);
        }

        if (!candles.isEmpty()) {
            repo.saveAll(candles);
            log.debug("Flushed {} minute candles", candles.size());
        }
    }

    private static BigDecimal bd(double v) {
        return Double.isNaN(v) || Double.isInfinite(v)
                ? BigDecimal.ZERO
                : BigDecimal.valueOf(v).setScale(4, RoundingMode.HALF_UP);
    }
}
