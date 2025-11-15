"""
Rate limiting middleware using slowapi with Redis support.
"""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from core.config import get_settings
from core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Determine rate limit based on environment
# In development/localhost, use much higher limits or disable
if settings.DEBUG or settings.ENVIRONMENT.lower() in ["development", "dev", "local"]:
    # Very high limits for local development (effectively unlimited)
    rate_limit = "10000/minute"  # 10,000 requests per minute for local dev
    logger.info(f"Rate limiting configured for development: {rate_limit}")
else:
    # Production limits
    rate_limit = f"{settings.RATE_LIMIT_PER_MINUTE}/minute"
    logger.info(f"Rate limiting configured for production: {rate_limit}")

# Initialize rate limiter
# SECURITY: Rate limiting to prevent brute force and DoS attacks
try:
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    redis_client.ping()  # Test connection
    # Use Redis for distributed rate limiting
    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri=settings.REDIS_URL,
        default_limits=[rate_limit]
    )
    logger.info("Rate limiting initialized with Redis")
except Exception as e:
    # Fallback to in-memory if Redis fails
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[rate_limit]
    )
    logger.warning(f"Rate limiting initialized with in-memory storage (Redis not available): {str(e)}")

# Export limiter for use in route decorators
__all__ = ['limiter', '_rate_limit_exceeded_handler', 'RateLimitExceeded']

