"""
CSRF Protection Utilities

Provides CSRF token generation and validation to prevent Cross-Site Request Forgery attacks.
"""

import secrets
from typing import Optional
from fastapi import Request, HTTPException, status
from core.config import get_settings
from core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Try to use Redis for CSRF token storage, fallback to in-memory
try:
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    redis_client.ping()
    USE_REDIS = True
    logger.info("CSRF protection using Redis for token storage")
except Exception as e:
    USE_REDIS = False
    # In-memory storage (fallback)
    _csrf_tokens: dict[str, str] = {}
    logger.warning(f"CSRF protection using in-memory storage (Redis not available): {str(e)}")


def generate_csrf_token() -> str:
    """
    Generate a secure CSRF token.
    
    Returns:
        str: URL-safe random token (32 bytes = 43 characters)
    """
    return secrets.token_urlsafe(32)


def store_csrf_token(user_id: str, token: str, expires_in: int = 3600) -> None:
    """
    Store CSRF token for a user.
    
    Args:
        user_id: User ID
        token: CSRF token
        expires_in: Expiration time in seconds (default: 1 hour)
    """
    key = f"csrf_token:{user_id}"
    
    if USE_REDIS:
        try:
            redis_client.setex(key, expires_in, token)
        except Exception as e:
            logger.error(f"Failed to store CSRF token in Redis: {str(e)}")
            raise
    else:
        # In-memory storage (simple expiration not implemented, but tokens are short-lived)
        _csrf_tokens[key] = token


def get_csrf_token(user_id: str) -> Optional[str]:
    """
    Get stored CSRF token for a user.
    
    Args:
        user_id: User ID
        
    Returns:
        Optional[str]: CSRF token if found, None otherwise
    """
    key = f"csrf_token:{user_id}"
    
    if USE_REDIS:
        try:
            return redis_client.get(key)
        except Exception as e:
            logger.error(f"Failed to get CSRF token from Redis: {str(e)}")
            return None
    else:
        return _csrf_tokens.get(key)


def delete_csrf_token(user_id: str) -> None:
    """
    Delete CSRF token for a user (e.g., on logout).
    
    Args:
        user_id: User ID
    """
    key = f"csrf_token:{user_id}"
    
    if USE_REDIS:
        try:
            redis_client.delete(key)
        except Exception as e:
            logger.error(f"Failed to delete CSRF token from Redis: {str(e)}")
    else:
        _csrf_tokens.pop(key, None)


def validate_csrf_token(request: Request, user_id: str) -> bool:
    """
    Validate CSRF token from request.
    
    Checks:
    1. CSRF token in cookie (csrf_token)
    2. CSRF token in header (X-CSRF-Token)
    3. Tokens match
    4. Token exists in storage for this user
    
    Args:
        request: FastAPI request object
        user_id: User ID
        
    Returns:
        bool: True if valid, False otherwise
    """
    # Get token from cookie
    cookie_token = request.cookies.get("csrf_token")
    
    # Get token from header
    header_token = request.headers.get("X-CSRF-Token")
    
    # Both must be present
    if not cookie_token or not header_token:
        logger.warning(
            f"CSRF validation failed: missing token (cookie={bool(cookie_token)}, header={bool(header_token)}, "
            f"user_id={user_id}, path={request.url.path})"
        )
        return False
    
    # Tokens must match
    if cookie_token != header_token:
        logger.warning(
            f"CSRF validation failed: tokens don't match (cookie_token={cookie_token[:10]}..., "
            f"header_token={header_token[:10]}..., user_id={user_id})"
        )
        return False
    
    # Token must exist in storage
    stored_token = get_csrf_token(user_id)
    if not stored_token or stored_token != cookie_token:
        logger.warning(
            f"CSRF validation failed: token not found in storage or doesn't match "
            f"(stored={bool(stored_token)}, cookie_token={cookie_token[:10] if cookie_token else None}..., "
            f"stored_token={stored_token[:10] if stored_token else None}..., user_id={user_id})"
        )
        return False
    
    return True


def require_csrf_token(request: Request, user_id: str) -> None:
    """
    Require valid CSRF token or raise HTTPException.
    
    Args:
        request: FastAPI request object
        user_id: User ID
        
    Raises:
        HTTPException: If CSRF token is invalid
    """
    if not validate_csrf_token(request, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing CSRF token"
        )

