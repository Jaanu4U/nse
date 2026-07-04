package com.nse.ingest.config;

import com.nse.ingest.engine.AlertEngine;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class AlertEngineConfig {
    @Bean
    public AlertEngine alertEngine() {
        return new AlertEngine();
    }
}
