"""
ClientHunt API - Main Application

FastAPI application entry point for the ClientHunt SaaS platform.
Handles authentication, subscriptions, payments, and opportunity management.

Port: 7300
"""

from fastapi import FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from core.config import get_settings
from core.logger import get_logger, setup_logging
from api.middleware.rate_limit import limiter, _rate_limit_exceeded_handler
from api.routes import auth, users, subscriptions, payments, keyword_searches, opportunities, usage, prices, cleanup, support, subscription_jobs, admin, csrf

# Initialize centralized logging (must be done before importing routes)
setup_logging()
logger = get_logger(__name__)

# Get settings
settings = get_settings()

# Initialize Sentry (if enabled)
if settings.SENTRY_ENABLED and settings.SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
            ],
            traces_sample_rate=0.1,  # 10% of transactions
            environment=settings.ENVIRONMENT,
            release=settings.APP_VERSION,
        )
        logger.info("Sentry error tracking initialized")
    except ImportError:
        logger.warning("Sentry SDK not installed. Install with: pip install sentry-sdk[fastapi]")
    except Exception as e:
        logger.warning(f"Failed to initialize Sentry: {str(e)}")

# Create FastAPI application
# SECURITY: Disable API docs in production to prevent exposing admin endpoints
app = FastAPI(
    title=settings.APP_NAME,
    description="Freelancer Opportunity Finder - Reddit-First Lead Generation Platform",
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.DEBUG else None,  # Only enable in development
    redoc_url="/redoc" if settings.DEBUG else None,  # Only enable in development
    openapi_url="/openapi.json" if settings.DEBUG else None  # Only enable in development
)

# Attach rate limiter to app state (for backward compatibility if needed)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS
# SECURITY: Restrict methods and headers to only what's needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],  # Only needed methods
    allow_headers=["Content-Type", "Authorization", "X-Service-Token", "X-CSRF-Token"],  # Include CSRF token header
)


# Request Size Limit Middleware
# SECURITY: Limit request body size to prevent DoS attacks
MAX_REQUEST_SIZE = settings.MAX_REQUEST_SIZE_MB * 1024 * 1024  # Convert MB to bytes

@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """
    Limit request body size to prevent DoS attacks.
    
    Checks Content-Length header and enforces maximum request size.
    Rejects requests that exceed the limit before processing.
    
    Note: This middleware checks the Content-Length header. For chunked transfer
    encoding or streaming requests without Content-Length, additional protection
    should be configured at the reverse proxy/load balancer level (e.g., Nginx).
    
    Args:
        request: Incoming HTTP request
        call_next: Next middleware/route handler
        
    Returns:
        Response: HTTP response
        
    Raises:
        HTTPException: 413 Payload Too Large if request exceeds limit
    """
    # Check Content-Length header if present
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            size = int(content_length)
            if size > MAX_REQUEST_SIZE:
                client_ip = request.client.host if request.client else "unknown"
                logger.warning(
                    f"Request rejected: Content-Length {size} bytes exceeds limit {MAX_REQUEST_SIZE} bytes. "
                    f"IP: {client_ip}, Path: {request.url.path}"
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": f"Request body too large. Maximum size is {settings.MAX_REQUEST_SIZE_MB}MB",
                        "error_code": "payload_too_large",
                        "max_size_mb": settings.MAX_REQUEST_SIZE_MB
                    }
                )
        except ValueError:
            # Invalid Content-Length header, let it through (will be caught by FastAPI validation)
            pass
    
    # For requests without Content-Length (chunked transfer encoding, streaming),
    # the body size is checked by FastAPI/Uvicorn during parsing.
    # Additional protection should be configured at the reverse proxy level (Nginx).
    response = await call_next(request)
    return response


# Security Headers Middleware
# SECURITY: Add security headers to all responses
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """
    Add security headers to all HTTP responses.
    
    Headers added:
    - Strict-Transport-Security: Enforce HTTPS
    - X-Content-Type-Options: Prevent MIME type sniffing
    - X-Frame-Options: Prevent clickjacking
    - X-XSS-Protection: Enable XSS filter
    - Referrer-Policy: Control referrer information
    """
    response = await call_next(request)
    
    # Only add HSTS in production (HTTPS required)
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    
    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"
    
    # Prevent clickjacking
    response.headers["X-Frame-Options"] = "DENY"
    
    # Enable XSS filter (legacy browsers)
    response.headers["X-XSS-Protection"] = "1; mode=block"
    
    # Control referrer information
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    
    # Content Security Policy (basic - can be enhanced per route if needed)
    # Note: CSP is complex and may need adjustment based on frontend requirements
    # For now, we'll keep it permissive but can tighten later
    if settings.ENVIRONMENT == "production":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "  # unsafe-eval needed for some libs
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self' https:; "
            "frame-ancestors 'none';"
        )
    
    return response


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint to verify API is running.
    
    Returns:
        dict: Status and version information
    """
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT
    }


# Sentry debug endpoint (for testing error tracking)
# Only available in development/staging environments
@app.get("/sentry-debug", tags=["Debug"])
async def trigger_error():
    """
    Debug endpoint to test Sentry error tracking.
    
    This endpoint intentionally triggers a division by zero error
    to verify that Sentry is capturing and reporting errors correctly.
    
    **Security**: Only available in development/staging environments.
    Disabled in production for security reasons.
    
    Returns:
        This endpoint will always raise an error (for testing purposes)
    """
    # Only allow in development/staging
    if settings.ENVIRONMENT == "production":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Endpoint not found"
        )
    
    # Intentionally trigger a division by zero error
    division_by_zero = 1 / 0
    return {"message": "This should never be reached"}


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """
    Root endpoint with API information.
    
    Returns:
        dict: API welcome message and documentation links
    """
    return {
        "message": f"Welcome to {settings.APP_NAME} API",
        "version": settings.APP_VERSION,
        "docs": f"{settings.API_URL}/docs",
        "health": f"{settings.API_URL}/health"
    }


# Include routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
app.include_router(subscriptions.router, prefix="/api/v1/subscriptions", tags=["Subscriptions"])
app.include_router(payments.router, prefix="/api/v1/payments", tags=["Payments"])
app.include_router(prices.router, prefix="/api/v1/prices", tags=["Prices"])
app.include_router(keyword_searches.router, prefix="/api/v1/keyword-searches", tags=["Keyword Searches"])
app.include_router(opportunities.router, prefix="/api/v1/opportunities", tags=["Opportunities"])
app.include_router(usage.router, prefix="/api/v1/usage", tags=["Usage"])
app.include_router(cleanup.router, prefix="/api/v1/cleanup", tags=["Cleanup"])
app.include_router(support.router, prefix="/api/v1/support", tags=["Support"])
app.include_router(subscription_jobs.router, prefix="/api/v1/subscription-jobs", tags=["Subscription Jobs"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(csrf.router, prefix="/api/v1", tags=["CSRF"])


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """
    Global exception handler for unhandled errors.
    
    Args:
        request: The request that caused the exception
        exc: The exception that was raised
        
    Returns:
        JSONResponse: Error response with details
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "message": str(exc) if settings.DEBUG else "An error occurred"
        }
    )


# Startup event
@app.on_event("startup")
async def startup_event():
    """
    Application startup event handler.
    Initializes connections and services.
    """
    # SECURITY: Fail fast if DEBUG is enabled in production
    if settings.ENVIRONMENT == "production" and settings.DEBUG:
        logger.critical("SECURITY ERROR: DEBUG=True in production environment!")
        logger.critical("This will leak sensitive error information. Set DEBUG=False immediately.")
        raise RuntimeError("DEBUG must be False in production. Check your environment variables.")
    
    logger.info(f"Starting {settings.APP_NAME} API v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"API URL: {settings.API_URL}")
    logger.info(f"Frontend URL: {settings.FRONTEND_URL}")
    logger.info(f"Rixly API URL: {settings.RIXLY_API_URL}")
    
    # Run database migrations on startup (for Docker convenience)
    # Note: In production, consider running migrations separately
    try:
        import subprocess
        import os
        # Only run migrations if AUTO_RUN_MIGRATIONS env var is set
        if os.getenv("AUTO_RUN_MIGRATIONS", "false").lower() == "true":
            logger.info("Running database migrations...")
            result = subprocess.run(
                ["alembic", "upgrade", "head"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd="/app"
            )
            if result.returncode == 0:
                logger.info("Database migrations completed successfully")
            else:
                logger.warning(f"Migration warning: {result.stderr}")
        else:
            logger.info("Skipping automatic migrations (set AUTO_RUN_MIGRATIONS=true to enable)")
    except Exception as e:
        logger.warning(f"Could not run migrations automatically: {str(e)}")
        logger.info("You may need to run migrations manually: docker-compose exec lead-api alembic upgrade head")


# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """
    Application shutdown event handler.
    Closes connections and cleans up resources.
    """
    logger.info(f"Shutting down {settings.APP_NAME} API")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=7300,
        reload=settings.DEBUG
    )
