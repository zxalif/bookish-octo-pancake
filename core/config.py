"""
Application Configuration

Centralized configuration management using Pydantic Settings.
Loads configuration from environment variables.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator, Field
from typing import List, Any
from functools import lru_cache
import json


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    All settings can be overridden via environment variables or .env file.
    """
    
    # Application
    APP_NAME: str = "ClientHunt"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    ENVIRONMENT: str = "development"
    
    # API Configuration
    API_URL: str = "http://localhost:7300"
    FRONTEND_URL: str = "http://localhost:9100"
    
    # Database
    DATABASE_URL: str = "postgresql://freelancehunt:freelancehunt123@localhost:5432/freelancehunt"
    
    # JWT Authentication
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30  # 30 days
    
    # Paddle Payment Gateway (PRIMARY)
    PADDLE_API_KEY: str = ""
    PADDLE_VENDOR_ID: str = ""
    PADDLE_ENVIRONMENT: str = "sandbox"  # or "live"
    PADDLE_WEBHOOK_SECRET: str = ""
    
    # Paddle Price IDs - DEPRECATED: Prices are now stored in the database
    # These are kept for backward compatibility but are no longer used
    # Prices should be populated via: python scripts/setup_paddle_products.py
    PADDLE_STARTER_MONTHLY_PRICE_ID: str = ""  # DEPRECATED: Use database
    PADDLE_PROFESSIONAL_MONTHLY_PRICE_ID: str = ""  # DEPRECATED: Use database
    PADDLE_POWER_MONTHLY_PRICE_ID: str = ""  # DEPRECATED: Use database
    PADDLE_STARTER_YEARLY_PRICE_ID: str = ""  # DEPRECATED: Use database
    PADDLE_PROFESSIONAL_YEARLY_PRICE_ID: str = ""  # DEPRECATED: Use database
    PADDLE_POWER_YEARLY_PRICE_ID: str = ""  # DEPRECATED: Use database
    PADDLE_STARTER_PRICE_ID: str = ""  # DEPRECATED: Use database
    PADDLE_PROFESSIONAL_PRICE_ID: str = ""  # DEPRECATED: Use database
    PADDLE_POWER_PRICE_ID: str = ""  # DEPRECATED: Use database
    
    # Rixly API Integration (for lead generation)
    RIXLY_API_URL: str = "http://localhost:8000"  # Rixly production port (7101 for dev)
    RIXLY_API_KEY: str = "dev_api_key"  # Default API key for development
    
    # Service Token (for scheduled jobs/cron authentication)
    SERVICE_TOKEN: str = ""  # Set in .env for production (e.g., generate with: openssl rand -hex 32)
    
    # Email Configuration
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@freelancehunt.com"
    SMTP_FROM_NAME: str = "ClientHunt"
    SMTP_NOREPLY_EMAIL: str = "noreply@clienthunt.app"
    SMTP_WELCOME_EMAIL: str = "welcome@clienthunt.app"
    
    # Redis Cache
    REDIS_URL: str = "redis://localhost:6379"
    
    # CORS
    # Store as string to avoid Pydantic Settings trying to parse as JSON
    # Will be converted to list via property
    # Use Field with alias to map CORS_ORIGINS env var to this field
    cors_origins_str: str = Field(
        default="http://localhost:9100,http://localhost:3000",
        alias="CORS_ORIGINS"
    )
    
    @property
    def CORS_ORIGINS(self) -> List[str]:
        """
        Get CORS origins as a list.
        
        Parses from string format (comma-separated or JSON).
        """
        value = getattr(self, 'cors_origins_str', "http://localhost:9100,http://localhost:3000")
        
        if not value or not isinstance(value, str):
            return ["http://localhost:9100", "http://localhost:3000"]
        
        # Try JSON first
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except (json.JSONDecodeError, TypeError):
            pass
        
        # Try comma-separated
        if "," in value:
            origins = [origin.strip() for origin in value.split(",") if origin.strip()]
            return origins if origins else ["http://localhost:9100", "http://localhost:3000"]
        
        # Single value
        return [value.strip()] if value.strip() else ["http://localhost:9100", "http://localhost:3000"]
    
    @model_validator(mode="before")
    @classmethod
    def parse_cors_origins_before(cls, data: Any) -> Any:
        """
        Handle CORS_ORIGINS before Pydantic tries to parse it.
        
        Converts lists to strings and handles empty values.
        The Field alias will map CORS_ORIGINS to cors_origins_str.
        """
        if not isinstance(data, dict):
            return data
        
        # Check for CORS_ORIGINS (the alias will handle mapping to cors_origins_str)
        if "CORS_ORIGINS" in data:
            cors_value = data["CORS_ORIGINS"]
            
            # If it's already a list, convert to comma-separated string
            if isinstance(cors_value, list):
                data["CORS_ORIGINS"] = ",".join(str(item) for item in cors_value)
            elif cors_value is None or cors_value == "":
                # Empty value, use default
                data["CORS_ORIGINS"] = "http://localhost:9100,http://localhost:3000"
            # If it's already a string, keep it as is (alias will map it)
        
        return data
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"
    
    # Monitoring (Sentry)
    SENTRY_DSN: str = ""
    SENTRY_ENABLED: bool = False
    
    # Subscription Plans Configuration
    # IMPORTANT: Keyword searches use HYBRID limits:
    # - keyword_searches: CONCURRENT limit (active + soft-deleted in current month)
    # - keyword_searches_created_per_month: MONTHLY limit (total created this month)
    # This prevents abuse while allowing legitimate experimentation
    PLAN_LIMITS: dict = {
        "free": {
            # Free tier for 1 month - limited to power user limits
            "keyword_searches": 10,  # Concurrent (active + soft-deleted this month)
            "keyword_searches_created_per_month": 20,  # Monthly creation limit
            "opportunities_per_month": 500,  # Same as power plan (500 per month)
            "api_calls_per_month": 0  # No API access for free tier
        },
        "starter": {
            "keyword_searches": 2,  # Concurrent (active + soft-deleted this month)
            "keyword_searches_created_per_month": 5,  # Monthly creation limit
            "opportunities_per_month": 50,  # Monthly limit
            "api_calls_per_month": 0
        },
        "professional": {
            "keyword_searches": 5,  # Concurrent (active + soft-deleted this month)
            "keyword_searches_created_per_month": 10,  # Monthly creation limit
            "opportunities_per_month": 200,  # Monthly limit
            "api_calls_per_month": 0
        },
        "power": {
            "keyword_searches": 10,  # Concurrent (active + soft-deleted this month)
            "keyword_searches_created_per_month": 20,  # Monthly creation limit
            "opportunities_per_month": 500,  # Monthly limit
            "api_calls_per_month": 1000
        }
    }
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        # Map CORS_ORIGINS env var to _cors_origins_str field
        env_prefix="",
        # Don't try to parse complex types as JSON
        env_parse_none_str=None
    )


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    
    Uses lru_cache to ensure settings are loaded only once.
    
    Returns:
        Settings: Application settings instance
    """
    return Settings()
