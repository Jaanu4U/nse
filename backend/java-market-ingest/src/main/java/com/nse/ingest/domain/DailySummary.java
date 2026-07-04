package com.nse.ingest.domain;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Entity
@Table(name = "delta_daily_summary", uniqueConstraints = @UniqueConstraint(columnNames = {"trade_date","symbol"}, name = "uq_delta_daily"))
public class DailySummary {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Long id;
    @Column(name = "trade_date", nullable = false) private LocalDate tradeDate;
    @Column(nullable = false, length = 20) private String symbol;
    @Column(name = "open_price",  precision = 14, scale = 4) private BigDecimal openPrice;
    @Column(name = "high_price",  precision = 14, scale = 4) private BigDecimal highPrice;
    @Column(name = "low_price",   precision = 14, scale = 4) private BigDecimal lowPrice;
    @Column(name = "close_price", precision = 14, scale = 4) private BigDecimal closePrice;
    @Column(name = "vwap",        precision = 18, scale = 6) private BigDecimal vwap;
    private Long volume;
    private Integer trades;
    @Column(name = "buy_volume")  private Long buyVolume;
    @Column(name = "sell_volume") private Long sellVolume;
    private Long delta;
    @Column(name = "cumulative_delta") private Long cumulativeDelta;
    @Column(name = "delivery_pct",    precision = 7, scale = 4) private BigDecimal deliveryPct;
    @Column(name = "prediction_score",precision = 7, scale = 4) private BigDecimal predictionScore;
    @Column(name = "bullish_prob",    precision = 7, scale = 4) private BigDecimal bullishProb;
    @Column(name = "bearish_prob",    precision = 7, scale = 4) private BigDecimal bearishProb;
    @Column(name = "confidence_score",precision = 7, scale = 4) private BigDecimal confidenceScore;
    @Column(name = "created_at") private LocalDateTime createdAt;
    @PrePersist protected void onCreate() { this.createdAt = LocalDateTime.now(); }

    public Long getId() { return id; }
    public LocalDate getTradeDate() { return tradeDate; }
    public void setTradeDate(LocalDate v) { this.tradeDate = v; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String v) { this.symbol = v; }
    public BigDecimal getOpenPrice() { return openPrice; }
    public void setOpenPrice(BigDecimal v) { this.openPrice = v; }
    public BigDecimal getHighPrice() { return highPrice; }
    public void setHighPrice(BigDecimal v) { this.highPrice = v; }
    public BigDecimal getLowPrice() { return lowPrice; }
    public void setLowPrice(BigDecimal v) { this.lowPrice = v; }
    public BigDecimal getClosePrice() { return closePrice; }
    public void setClosePrice(BigDecimal v) { this.closePrice = v; }
    public BigDecimal getVwap() { return vwap; }
    public void setVwap(BigDecimal v) { this.vwap = v; }
    public Long getVolume() { return volume; }
    public void setVolume(Long v) { this.volume = v; }
    public Integer getTrades() { return trades; }
    public void setTrades(Integer v) { this.trades = v; }
    public Long getBuyVolume() { return buyVolume; }
    public void setBuyVolume(Long v) { this.buyVolume = v; }
    public Long getSellVolume() { return sellVolume; }
    public void setSellVolume(Long v) { this.sellVolume = v; }
    public Long getDelta() { return delta; }
    public void setDelta(Long v) { this.delta = v; }
    public Long getCumulativeDelta() { return cumulativeDelta; }
    public void setCumulativeDelta(Long v) { this.cumulativeDelta = v; }
    public BigDecimal getDeliveryPct() { return deliveryPct; }
    public void setDeliveryPct(BigDecimal v) { this.deliveryPct = v; }
    public BigDecimal getPredictionScore() { return predictionScore; }
    public void setPredictionScore(BigDecimal v) { this.predictionScore = v; }
    public BigDecimal getBullishProb() { return bullishProb; }
    public void setBullishProb(BigDecimal v) { this.bullishProb = v; }
    public BigDecimal getBearishProb() { return bearishProb; }
    public void setBearishProb(BigDecimal v) { this.bearishProb = v; }
    public BigDecimal getConfidenceScore() { return confidenceScore; }
    public void setConfidenceScore(BigDecimal v) { this.confidenceScore = v; }
    public LocalDateTime getCreatedAt() { return createdAt; }
}
