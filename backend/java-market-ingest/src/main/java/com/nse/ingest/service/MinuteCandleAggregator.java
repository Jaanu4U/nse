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
        // PERSIST WINDOW GUARD: the cron fires 03:00–10:59 UTC (08:30–16:29 IST)
        // but candles must only be written for the live session. A flush at
        // HH:mm captures the bar ending that minute, so the valid flush window
        // is 09:16–15:31 IST (bars 09:15–15:30).
        java.time.LocalTime istNow = java.time.LocalTime.now(java.time.ZoneId.of("Asia/Kolkata"));
        if (istNow.isBefore(java.time.LocalTime.of(9, 16)) || istNow.isAfter(java.time.LocalTime.of(15, 31))) {
            return;
        }

        LocalDateTime now     = LocalDateTime.now();
        LocalDate     today   = now.toLocalDate();
        List<MinuteCandle> candles = new ArrayList<>(registry.symbolCount());

        for (SymbolState state : registry.all()) {
            // Capture per-minute buy/sell/delta BEFORE resetting the rate window
            long minuteBuy   = state.getDeltaEngine().getBuyVolume();
            long minuteSell  = state.getDeltaEngine().getSellVolume();
            long minuteDelta = state.getDeltaEngine().getDelta();

            // D1/D2 FIX: true per-minute OHLC + per-minute volume
            SymbolState.MinuteBar bar = state.snapshotAndResetMinuteBar();
            long barDelta = state.getDeltaEngine().snapshotAndResetRate();

            // D4 FIX: skip minutes with no trades (previously wrote a stale row
            // for every symbol whose DAY volume was > 0 — 88% dead rows)
            if (bar.volume() == 0) continue;

            MinuteCandle c = new MinuteCandle();
            c.setSymbol(state.getSymbol());
            c.setTradeDate(today);
            c.setMinuteTs(now.withSecond(0).withNano(0));
            c.setOpenPrice(bd(bar.open()));
            c.setHighPrice(bd(bar.high()));
            c.setLowPrice(bd(bar.low()));
            c.setClosePrice(bd(bar.close()));
            c.setVolume(bar.volume());                 // per-minute volume
            c.setDayVolume(state.getTotalVolume());    // cumulative day volume (separate column)
            c.setBuyVolume(minuteBuy);
            c.setSellVolume(minuteSell);
            c.setDelta(minuteDelta);
            candles.add(c);

            // Update rolling windows with the completed minute bar
            long epochTs = now.toEpochSecond(java.time.ZoneOffset.UTC);
            state.window1m.push(epochTs, bar.open(), bar.high(), bar.low(), bar.close(),
                bar.volume(), minuteBuy, minuteSell, barDelta);
            state.window5m.push(epochTs, bar.open(), bar.high(), bar.low(), bar.close(),
                bar.volume(), minuteBuy, minuteSell, barDelta);
            state.window15m.push(epochTs, bar.open(), bar.high(), bar.low(), bar.close(),
                bar.volume(), minuteBuy, minuteSell, barDelta);
            state.windowDay.push(epochTs, bar.open(), bar.high(), bar.low(), bar.close(),
                bar.volume(), minuteBuy, minuteSell, barDelta);
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
