package com.nse.ingest.repo;

import com.nse.ingest.domain.MinuteCandle;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;

@Repository
public interface MinuteCandleRepository extends JpaRepository<MinuteCandle, Long> {
    List<MinuteCandle> findBySymbolAndTradeDateOrderByMinuteTsAsc(String symbol, LocalDate tradeDate);

    @Modifying
    @Query("DELETE FROM MinuteCandle m WHERE m.tradeDate < :cutoff")
    int deleteOlderThan(LocalDate cutoff);
}
