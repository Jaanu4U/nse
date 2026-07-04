package com.nse.ingest.service;

import com.nse.ingest.domain.DailySummary;
import com.nse.ingest.domain.PredictionHistory;
import com.nse.ingest.dto.PredictionResultDto;
import com.nse.ingest.engine.PredictionEngine;
import com.nse.ingest.repo.DailySummaryRepository;
import com.nse.ingest.repo.PredictionHistoryRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * Called at 15:31 to persist one {@link DailySummary} and one
 * {@link PredictionHistory} record per symbol, then wipe all in-memory state
 * at 15:35 so memory is fully reclaimed.
 */
@Service
public class EodService {

    private static final Logger log = LoggerFactory.getLogger(EodService.class);

    private final MarketStateRegistry       registry;
    private final DailySummaryRepository    dailyRepo;
    private final PredictionHistoryRepository predRepo;
    private final PredictionEngine          predEngine;

    public EodService(MarketStateRegistry registry,
                      DailySummaryRepository dailyRepo,
                      PredictionHistoryRepository predRepo,
                      PredictionEngine predEngine) {
        this.registry   = registry;
        this.dailyRepo  = dailyRepo;
        this.predRepo   = predRepo;
        this.predEngine = predEngine;
    }

    @Transactional
    public void saveEod() {
        LocalDate today = LocalDate.now();
        log.info("Starting EOD save for {}", today);

        List<DailySummary>    summaries   = new ArrayList<>(registry.symbolCount());
        List<PredictionHistory> predictions = new ArrayList<>(registry.symbolCount());

        for (SymbolState state : registry.all()) {
            if (state.getTotalVolume() == 0) continue;

            PredictionResultDto pred = predEngine.score(state);

            DailySummary ds = new DailySummary();
            ds.setTradeDate(today);
            ds.setSymbol(state.getSymbol());
            ds.setOpenPrice(bd(state.getOpen()));
            ds.setHighPrice(bd(state.getHigh()));
            ds.setLowPrice(bd(state.getLow()));
            ds.setClosePrice(bd(state.getLtp()));
            ds.setVwap(bd(state.getVwap()));
            ds.setVolume(state.getTotalVolume());
            ds.setTrades(state.getTradeCount());
            ds.setBuyVolume(state.getDeltaEngine().getBuyVolume());
            ds.setSellVolume(state.getDeltaEngine().getSellVolume());
            ds.setDelta(state.getDeltaEngine().getDelta());
            ds.setCumulativeDelta(state.getDeltaEngine().getCumulativeDelta());
            ds.setPredictionScore(bd(pred.predictionScore()));
            ds.setBullishProb(bd4(pred.bullishProbability()));
            ds.setBearishProb(bd4(pred.bearishProbability()));
            ds.setConfidenceScore(bd4(pred.confidenceScore()));
            summaries.add(ds);

            PredictionHistory ph = new PredictionHistory();
            ph.setSymbol(state.getSymbol());
            ph.setTradeDate(today);
            ph.setScoredAt(LocalDateTime.now());
            ph.setPredictionScore(bd(pred.predictionScore()));
            ph.setBullishProb(bd4(pred.bullishProbability()));
            ph.setBearishProb(bd4(pred.bearishProbability()));
            ph.setConfidenceScore(bd4(pred.confidenceScore()));
            ph.setDeltaInput(state.getDeltaEngine().getDelta());
            ph.setCumDeltaInput(state.getDeltaEngine().getCumulativeDelta());
            ph.setVwapInput(bd(state.getVwap()));
            ph.setVolumeInput(state.getTotalVolume());
            ph.setRsiInput(bd4(state.getRsi()));
            predictions.add(ph);
        }

        dailyRepo.saveAll(summaries);
        predRepo.saveAll(predictions);
        log.info("EOD save complete: {} symbols persisted", summaries.size());
    }

    /** Called at 15:35 — clears all in-memory tick state. */
    public void cleanupMemory() {
        registry.clearAll();
        log.info("In-memory tick state cleared after EOD");
    }

    private static BigDecimal bd(double v) {
        return Double.isNaN(v) || Double.isInfinite(v)
                ? BigDecimal.ZERO
                : BigDecimal.valueOf(v).setScale(4, RoundingMode.HALF_UP);
    }

    private static BigDecimal bd4(double v) {
        return BigDecimal.valueOf(Math.round(v * 10000.0) / 10000.0).setScale(4, RoundingMode.HALF_UP);
    }
}
