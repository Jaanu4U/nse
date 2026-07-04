package com.nse.ingest.domain;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Entity
@Table(name = "delta_prediction_history")
public class PredictionHistory {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Long id;
    @Column(nullable = false, length = 20) private String symbol;
    @Column(name = "trade_date", nullable = false) private LocalDate tradeDate;
    @Column(name = "scored_at",  nullable = false) private LocalDateTime scoredAt;
    @Column(name = "prediction_score", precision = 7, scale = 4) private BigDecimal predictionScore;
    @Column(name = "bullish_prob",     precision = 7, scale = 4) private BigDecimal bullishProb;
    @Column(name = "bearish_prob",     precision = 7, scale = 4) private BigDecimal bearishProb;
    @Column(name = "confidence_score", precision = 7, scale = 4) private BigDecimal confidenceScore;
    @Column(name = "delta_input")  private Long deltaInput;
    @Column(name = "cum_delta_input") private Long cumDeltaInput;
    @Column(name = "vwap_input",  precision = 14, scale = 4) private BigDecimal vwapInput;
    @Column(name = "volume_input") private Long volumeInput;
    @Column(name = "rsi_input",   precision = 7, scale = 4) private BigDecimal rsiInput;
    @Column(name = "atr_input",   precision = 10, scale = 4) private BigDecimal atrInput;

    public Long getId() { return id; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String v) { this.symbol = v; }
    public LocalDate getTradeDate() { return tradeDate; }
    public void setTradeDate(LocalDate v) { this.tradeDate = v; }
    public LocalDateTime getScoredAt() { return scoredAt; }
    public void setScoredAt(LocalDateTime v) { this.scoredAt = v; }
    public BigDecimal getPredictionScore() { return predictionScore; }
    public void setPredictionScore(BigDecimal v) { this.predictionScore = v; }
    public BigDecimal getBullishProb() { return bullishProb; }
    public void setBullishProb(BigDecimal v) { this.bullishProb = v; }
    public BigDecimal getBearishProb() { return bearishProb; }
    public void setBearishProb(BigDecimal v) { this.bearishProb = v; }
    public BigDecimal getConfidenceScore() { return confidenceScore; }
    public void setConfidenceScore(BigDecimal v) { this.confidenceScore = v; }
    public Long getDeltaInput() { return deltaInput; }
    public void setDeltaInput(Long v) { this.deltaInput = v; }
    public Long getCumDeltaInput() { return cumDeltaInput; }
    public void setCumDeltaInput(Long v) { this.cumDeltaInput = v; }
    public BigDecimal getVwapInput() { return vwapInput; }
    public void setVwapInput(BigDecimal v) { this.vwapInput = v; }
    public Long getVolumeInput() { return volumeInput; }
    public void setVolumeInput(Long v) { this.volumeInput = v; }
    public BigDecimal getRsiInput() { return rsiInput; }
    public void setRsiInput(BigDecimal v) { this.rsiInput = v; }
    public BigDecimal getAtrInput() { return atrInput; }
    public void setAtrInput(BigDecimal v) { this.atrInput = v; }
}
