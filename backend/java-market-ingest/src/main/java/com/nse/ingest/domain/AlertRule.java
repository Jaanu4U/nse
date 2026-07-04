package com.nse.ingest.domain;

import jakarta.persistence.*;
import java.time.LocalDateTime;

@Entity
@Table(name = "delta_alert_rules")
public class AlertRule {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Long id;
    @Column(nullable = false, length = 20) private String symbol;
    @Column(nullable = false, length = 40) private String type;
    @Column(nullable = false)              private double threshold;
    @Column(nullable = false)              private boolean once;
    @Column(nullable = false)              private boolean active = true;
    @Column(name = "created_at")           private LocalDateTime createdAt;
    @PrePersist protected void onCreate() { createdAt = LocalDateTime.now(); }

    public Long    getId()        { return id; }
    public String  getSymbol()    { return symbol; }
    public void    setSymbol(String v)    { this.symbol = v; }
    public String  getType()      { return type; }
    public void    setType(String v)      { this.type = v; }
    public double  getThreshold() { return threshold; }
    public void    setThreshold(double v) { this.threshold = v; }
    public boolean isOnce()       { return once; }
    public void    setOnce(boolean v)     { this.once = v; }
    public boolean isActive()     { return active; }
    public void    setActive(boolean v)   { this.active = v; }
    public LocalDateTime getCreatedAt()   { return createdAt; }
}
