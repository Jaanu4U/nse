package com.nse.ingest.repo;

import com.nse.ingest.domain.DailySummary;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.Optional;

@Repository
public interface DailySummaryRepository extends JpaRepository<DailySummary, Long> {
    Optional<DailySummary> findByTradeDateAndSymbol(LocalDate tradeDate, String symbol);
}
