import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "NSE Stock Analysis Platform"
    API_V1_STR: str = "/api/v1"
    
    # Database configuration
    DATABASE_URL: str = "postgresql://postgres:postgrespassword@localhost:5432/nse_stock_db"
    
    # Redis configuration
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # JWT Auth settings
    JWT_SECRET: str = "supersecretkeychangeinproduction1234567890"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 Days
    
    # Gemini AI settings
    GEMINI_API_KEY: str = ""
    
    # CORS Origins
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )

settings = Settings()
