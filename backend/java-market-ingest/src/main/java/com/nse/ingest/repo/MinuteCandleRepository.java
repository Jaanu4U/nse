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

    /** Per-symbol latest stored minute for a trade date — used by gap-fill to avoid duplicates. */
    @Query("SELECT m.symbol, MAX(m.minuteTs) FROM MinuteCandle m WHERE m.tradeDate = :date GROUP BY m.symbol")
    List<Object[]> maxMinuteTsBySymbol(LocalDate date);

    /**
     * Per-symbol day aggregates for startup rehydration: first open, day high/low,
     * last close, total volume, buy/sell split, and the VWAP numerator
     * (sum of typical price × volume).
     */
    @Query(value = """
        SELECT symbol,
               (ARRAY_AGG(open_price ORDER BY minute_ts ASC))[1]   AS open_price,
               MAX(high_price)                                     AS high_price,
               MIN(low_price)                                      AS low_price,
               (ARRAY_AGG(close_price ORDER BY minute_ts DESC))[1] AS close_price,
               COALESCE(SUM(volume), 0)                            AS volume,
               COALESCE(SUM(buy_volume), 0)                        AS buy_volume,
               COALESCE(SUM(sell_volume), 0)                       AS sell_volume,
               COALESCE(SUM(((high_price + low_price + close_price) / 3) * volume), 0) AS vwap_num
        FROM delta_minute_candle
        WHERE trade_date = :date
        GROUP BY symbol
        """, nativeQuery = true)
    List<Object[]> aggregateDayBySymbol(LocalDate date);

    @Modifying
    @Query("DELETE FROM MinuteCandle m WHERE m.tradeDate < :cutoff")
    int deleteOlderThan(LocalDate cutoff);
}
