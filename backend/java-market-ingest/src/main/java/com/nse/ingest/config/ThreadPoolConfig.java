package com.nse.ingest.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.task.AsyncTaskExecutor;
import org.springframework.core.task.support.TaskExecutorAdapter;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;

import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

@Configuration
public class ThreadPoolConfig {

    /**
     * Virtual-thread executor for all async work (virtual threads backed by carrier threads).
     */
    @Bean(name = "virtualThreadExecutor")
    public ExecutorService virtualThreadExecutor() {
        return Executors.newVirtualThreadPerTaskExecutor();
    }

    /**
     * Tick dispatch executor — dedicated virtual-thread pool for processing incoming ticks.
     */
    @Bean(name = "tickExecutor")
    public Executor tickExecutor() {
        return Executors.newVirtualThreadPerTaskExecutor();
    }

    /**
     * Scheduler thread pool for cron jobs (market open/close lifecycle).
     */
    @Bean
    public ThreadPoolTaskScheduler marketTaskScheduler() {
        ThreadPoolTaskScheduler scheduler = new ThreadPoolTaskScheduler();
        scheduler.setPoolSize(4);
        scheduler.setThreadNamePrefix("market-scheduler-");
        scheduler.initialize();
        return scheduler;
    }
}
