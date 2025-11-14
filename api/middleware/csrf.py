"""
CSRF Protection Middleware

Validates CSRF tokens on state-changing HTTP methods (POST, PUT, DELETE, PATCH).
"""

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from core.logger import get_logger

logger = get_logger(__name__)

# State-changing methods that require CSRF protection
PROTECTED_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Endpoints that should be excluded from CSRF protection
EXCLUDED_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/csrf-token",  # CSRF token endpoint itself
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    """
    CSRF Protection Middleware.
    
    Validates CSRF tokens on state-changing requests (POST, PUT, DELETE, PATCH).
    Excludes authentication endpoints and public endpoints.
    
    Note: This middleware validates CSRF tokens for authenticated users only.
    The actual validation is done in route dependencies using `require_csrf_protection`.
    """
    
    async def dispatch(self, request: Request, call_next):
        # Skip CSRF check for excluded paths
        if request.url.path in EXCLUDED_PATHS or any(request.url.path.startswith(path) for path in EXCLUDED_PATHS):
            return await call_next(request)
        
        # CSRF validation is handled in route dependencies
        # This middleware is a placeholder for future enhancements
        response = await call_next(request)
        return response

