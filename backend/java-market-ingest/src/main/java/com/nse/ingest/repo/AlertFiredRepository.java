package com.nse.ingest.repo;

import com.nse.ingest.domain.AlertFired;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;
import java.time.LocalDateTime;
import java.util.List;

@Repository
public interface AlertFiredRepository extends JpaRepository<AlertFired, Long> {
    List<AlertFired> findBySymbolOrderByFiredAtDesc(String symbol);
    List<AlertFired> findByFiredAtAfterOrderByFiredAtDesc(LocalDateTime since);
}
