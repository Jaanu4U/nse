package com.nse.ingest.domain;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Entity
@Table(name = "delta_minute_candle", indexes = {
    @Index(name = "idx_mc_symbol_date", columnList = "symbol,trade_date")
})
public class MinuteCandle {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Long id;
    @Column(nullable = false, length = 20) private String symbol;
    @Column(name = "trade_date", nullable = false) private LocalDate tradeDate;
    @Column(name = "minute_ts", nullable = false) private LocalDateTime minuteTs;
    @Column(name = "open_price",  precision = 14, scale = 4) private BigDecimal openPrice;
    @Column(name = "high_price",  precision = 14, scale = 4) private BigDecimal highPrice;
    @Column(name = "low_price",   precision = 14, scale = 4) private BigDecimal lowPrice;
    @Column(name = "close_price", precision = 14, scale = 4) private BigDecimal closePrice;
    private Long volume;
    @Column(name = "day_volume")  private Long dayVolume;
    @Column(name = "buy_volume")  private Long buyVolume;
    @Column(name = "sell_volume") private Long sellVolume;
    private Long delta;

    public Long getId() { return id; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String v) { this.symbol = v; }
    public LocalDate getTradeDate() { return tradeDate; }
    public void setTradeDate(LocalDate v) { this.tradeDate = v; }
    public LocalDateTime getMinuteTs() { return minuteTs; }
    public void setMinuteTs(LocalDateTime v) { this.minuteTs = v; }
    public BigDecimal getOpenPrice() { return openPrice; }
    public void setOpenPrice(BigDecimal v) { this.openPrice = v; }
    public BigDecimal getHighPrice() { return highPrice; }
    public void setHighPrice(BigDecimal v) { this.highPrice = v; }
    public BigDecimal getLowPrice() { return lowPrice; }
    public void setLowPrice(BigDecimal v) { this.lowPrice = v; }
    public BigDecimal getClosePrice() { return closePrice; }
    public void setClosePrice(BigDecimal v) { this.closePrice = v; }
    public Long getVolume() { return volume; }
    public void setVolume(Long v) { this.volume = v; }
    public Long getDayVolume() { return dayVolume; }
    public void setDayVolume(Long v) { this.dayVolume = v; }
    public Long getBuyVolume() { return buyVolume; }
    public void setBuyVolume(Long v) { this.buyVolume = v; }
    public Long getSellVolume() { return sellVolume; }
    public void setSellVolume(Long v) { this.sellVolume = v; }
    public Long getDelta() { return delta; }
    public void setDelta(Long v) { this.delta = v; }
}
