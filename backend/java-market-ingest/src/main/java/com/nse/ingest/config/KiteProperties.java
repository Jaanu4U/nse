package com.nse.ingest.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "kite")
public class KiteProperties {
    private String apiKey;
    private String apiSecret;
    private String accessToken;
    private String baseUrl = "https://api.kite.trade";
    private String wsUrl = "wss://ws.kite.trade";
    private long reconnectIntervalMs = 5000;
    private long heartbeatIntervalMs = 25000;
    private String loginUrl;

    public String getApiKey() { return apiKey; }
    public void setApiKey(String apiKey) { this.apiKey = apiKey; }
    public String getApiSecret() { return apiSecret; }
    public void setApiSecret(String apiSecret) { this.apiSecret = apiSecret; }
    public String getAccessToken() { return accessToken; }
    public void setAccessToken(String accessToken) { this.accessToken = accessToken; }
    public String getBaseUrl() { return baseUrl; }
    public void setBaseUrl(String baseUrl) { this.baseUrl = baseUrl; }
    public String getWsUrl() { return wsUrl; }
    public void setWsUrl(String wsUrl) { this.wsUrl = wsUrl; }
    public long getReconnectIntervalMs() { return reconnectIntervalMs; }
    public void setReconnectIntervalMs(long v) { this.reconnectIntervalMs = v; }
    public long getHeartbeatIntervalMs() { return heartbeatIntervalMs; }
    public void setHeartbeatIntervalMs(long v) { this.heartbeatIntervalMs = v; }
    public String getLoginUrl() { return loginUrl; }
    public void setLoginUrl(String loginUrl) { this.loginUrl = loginUrl; }
}
