"""
Redis Client for Caching and Job Tracking

Provides a singleton Redis client instance for use across the application.
"""
import redis
from typing import Optional
from functools import lru_cache
from core.config import get_settings
from core.logger import get_logger

logger = get_logger(__name__)

# Global Redis client instance
_redis_client: Optional[redis.Redis] = None


@lru_cache()
def get_redis_client() -> Optional[redis.Redis]:
    """
    Get Redis client instance (singleton).
    
    Returns:
        Redis client instance or None if Redis is not available
    """
    global _redis_client
    
    if _redis_client is not None:
        return _redis_client
    
    settings = get_settings()
    
    try:
        # Parse Redis URL
        redis_url = settings.REDIS_URL
        
        # Create Redis client
        _redis_client = redis.from_url(
            redis_url,
            decode_responses=True,  # Automatically decode responses to strings
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30
        )
        
        # Test connection
        _redis_client.ping()
        logger.info("Redis connection established")
        
        return _redis_client
        
    except Exception as e:
        logger.warning(f"Redis connection failed: {str(e)}. Falling back to in-memory storage.")
        return None


def is_redis_available() -> bool:
    """
    Check if Redis is available.
    
    Returns:
        bool: True if Redis is available, False otherwise
    """
    client = get_redis_client()
    if client is None:
        return False
    
    try:
        client.ping()
        return True
    except Exception:
        return False

