package com.nse.ingest.domain;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Entity
@Table(name = "delta_prices_daily",
       uniqueConstraints = @UniqueConstraint(columnNames = {"symbol","trade_date"},
                                             name = "uq_dpd_symbol_date"))
public class PriceBar {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Long id;
    @Column(nullable = false, length = 20) private String symbol;
    @Column(name = "trade_date", nullable = false) private LocalDate tradeDate;
    @Column(name = "open_price",  precision = 14, scale = 4) private BigDecimal openPrice;
    @Column(name = "high_price",  precision = 14, scale = 4) private BigDecimal highPrice;
    @Column(name = "low_price",   precision = 14, scale = 4) private BigDecimal lowPrice;
    @Column(name = "close_price", precision = 14, scale = 4) private BigDecimal closePrice;
    private Long volume;
    @Column(length = 10) private String source = "KITE";
    @Column(name = "loaded_at") private LocalDateTime loadedAt;
    @PrePersist protected void onCreate() { loadedAt = LocalDateTime.now(); }

    public Long       getId()          { return id; }
    public String     getSymbol()      { return symbol; }
    public void       setSymbol(String v)       { this.symbol = v; }
    public LocalDate  getTradeDate()   { return tradeDate; }
    public void       setTradeDate(LocalDate v) { this.tradeDate = v; }
    public BigDecimal getOpenPrice()   { return openPrice; }
    public void       setOpenPrice(BigDecimal v)  { this.openPrice = v; }
    public BigDecimal getHighPrice()   { return highPrice; }
    public void       setHighPrice(BigDecimal v)  { this.highPrice = v; }
    public BigDecimal getLowPrice()    { return lowPrice; }
    public void       setLowPrice(BigDecimal v)   { this.lowPrice = v; }
    public BigDecimal getClosePrice()  { return closePrice; }
    public void       setClosePrice(BigDecimal v) { this.closePrice = v; }
    public Long       getVolume()      { return volume; }
    public void       setVolume(Long v)          { this.volume = v; }
    public String     getSource()      { return source; }
    public void       setSource(String v)        { this.source = v; }
}
