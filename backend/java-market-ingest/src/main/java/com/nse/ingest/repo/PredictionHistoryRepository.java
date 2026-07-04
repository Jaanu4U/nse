package com.nse.ingest.repo;

import com.nse.ingest.domain.PredictionHistory;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;

@Repository
public interface PredictionHistoryRepository extends JpaRepository<PredictionHistory, Long> {
    List<PredictionHistory> findBySymbolAndTradeDateOrderByScoredAtDesc(String symbol, LocalDate tradeDate);
}
