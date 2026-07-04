package com.nse.ingest.domain;

import jakarta.persistence.*;
import java.time.LocalDateTime;

@Entity
@Table(name = "delta_alert_fired")
public class AlertFired {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Long id;
    @Column(name = "rule_id")                              private Long ruleId;
    @Column(nullable = false, length = 20)                 private String symbol;
    @Column(nullable = false, length = 40)                 private String type;
    @Column(name = "triggered_value", nullable = false)    private double triggeredValue;
    @Column(nullable = false)                              private double threshold;
    @Column(name = "fired_at", nullable = false)           private LocalDateTime firedAt;

    public Long getId() { return id; }
    public Long getRuleId() { return ruleId; }
    public void setRuleId(Long v) { this.ruleId = v; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String v) { this.symbol = v; }
    public String getType() { return type; }
    public void setType(String v) { this.type = v; }
    public double getTriggeredValue() { return triggeredValue; }
    public void setTriggeredValue(double v) { this.triggeredValue = v; }
    public double getThreshold() { return threshold; }
    public void setThreshold(double v) { this.threshold = v; }
    public LocalDateTime getFiredAt() { return firedAt; }
    public void setFiredAt(LocalDateTime v) { this.firedAt = v; }
}
