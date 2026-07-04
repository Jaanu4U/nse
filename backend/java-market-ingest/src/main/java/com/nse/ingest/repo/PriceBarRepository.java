package com.nse.ingest.repo;

import com.nse.ingest.domain.PriceBar;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;

@Repository
public interface PriceBarRepository extends JpaRepository<PriceBar, Long> {

    /** Fetch bars for a symbol ordered ascending — used for indicator warm-up. */
    List<PriceBar> findBySymbolOrderByTradeDateAsc(String symbol);

    /** Fetch bars in a date range. */
    List<PriceBar> findBySymbolAndTradeDateBetweenOrderByTradeDateAsc(
        String symbol, LocalDate from, LocalDate to);

    /** Latest loaded date for a symbol (to skip re-fetching). */
    @Query("SELECT MAX(p.tradeDate) FROM PriceBar p WHERE p.symbol = :symbol")
    LocalDate maxDate(String symbol);

    /** Count of symbols that have at least one bar loaded. */
    @Query("SELECT COUNT(DISTINCT p.symbol) FROM PriceBar p")
    long countDistinctSymbols();
}
