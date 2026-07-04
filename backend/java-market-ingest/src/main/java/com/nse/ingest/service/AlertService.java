package com.nse.ingest.service;

import com.nse.ingest.domain.AlertFired;
import com.nse.ingest.dto.AlertFiredDto;
import com.nse.ingest.dto.AlertRuleRequest;
import com.nse.ingest.engine.AlertEngine;
import com.nse.ingest.repo.AlertFiredRepository;
import com.nse.ingest.repo.AlertRuleRepository;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.function.Consumer;

/**
 * Bridges the in-memory AlertEngine with the database:
 *  - Loads active rules from DB on startup
 *  - Persists every fired alert asynchronously
 *  - Provides the SSE listener registration point
 */
@Service
public class AlertService {

    private static final Logger log = LoggerFactory.getLogger(AlertService.class);

    private final AlertEngine         engine;
    private final AlertRuleRepository ruleRepo;
    private final AlertFiredRepository firedRepo;

    public AlertService(AlertEngine engine,
                         AlertRuleRepository ruleRepo,
                         AlertFiredRepository firedRepo) {
        this.engine    = engine;
        this.ruleRepo  = ruleRepo;
        this.firedRepo = firedRepo;
    }

    @PostConstruct
    public void loadRulesFromDb() {
        List<com.nse.ingest.domain.AlertRule> dbRules = ruleRepo.findByActiveTrue();
        for (com.nse.ingest.domain.AlertRule r : dbRules) {
            try {
                AlertEngine.AlertType type = AlertEngine.AlertType.valueOf(r.getType());
                engine.addRule(new AlertEngine.AlertRule(r.getId(), r.getSymbol(), type, r.getThreshold(), r.isOnce()));
            } catch (Exception e) {
                log.warn("Skipping invalid rule {}: {}", r.getId(), e.getMessage());
            }
        }
        log.info("Loaded {} alert rules from DB", dbRules.size());
    }

    /** Register a listener that receives every alert the engine fires. */
    public void addListener(Consumer<AlertFiredDto> listener)    { engine.addListener(listener); }
    public void removeListener(Consumer<AlertFiredDto> listener) { engine.removeListener(listener); }

    /** Called from MarketStreamController to push alerts into SSE also into DB. */
    public void onAlertFired(AlertFiredDto dto) {
        Thread.ofVirtual().start(() -> {
            try {
                AlertFired af = new AlertFired();
                af.setRuleId(dto.ruleId());
                af.setSymbol(dto.symbol());
                af.setType(dto.type());
                af.setTriggeredValue(dto.value());
                af.setThreshold(dto.threshold());
                af.setFiredAt(dto.firedAt());
                firedRepo.save(af);
            } catch (Exception e) {
                log.error("Failed to persist alert", e);
            }
        });
    }

    @Transactional
    public com.nse.ingest.domain.AlertRule createRule(AlertRuleRequest req) {
        AlertEngine.AlertType type = AlertEngine.AlertType.valueOf(req.type());
        com.nse.ingest.domain.AlertRule r = new com.nse.ingest.domain.AlertRule();
        r.setSymbol(req.symbol().toUpperCase());
        r.setType(req.type());
        r.setThreshold(req.threshold());
        r.setOnce(req.once());
        r.setActive(true);
        r = ruleRepo.save(r);
        engine.addRule(new AlertEngine.AlertRule(r.getId(), r.getSymbol(), type, r.getThreshold(), r.isOnce()));
        return r;
    }

    @Transactional
    public void deleteRule(long id) {
        ruleRepo.findById(id).ifPresent(r -> { r.setActive(false); ruleRepo.save(r); });
        engine.removeRule(id);
    }

    public List<AlertEngine.AlertRule> liveRules() { return engine.getRules(); }
    public List<AlertFired> recentFired(int hours) {
        return firedRepo.findByFiredAtAfterOrderByFiredAtDesc(java.time.LocalDateTime.now().minusHours(hours));
    }
}
