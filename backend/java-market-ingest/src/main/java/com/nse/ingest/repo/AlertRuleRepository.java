package com.nse.ingest.repo;

import com.nse.ingest.domain.AlertRule;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;
import java.util.List;

@Repository
public interface AlertRuleRepository extends JpaRepository<AlertRule, Long> {
    List<AlertRule> findByActiveTrue();
    List<AlertRule> findBySymbolAndActiveTrue(String symbol);
}
