"""
CSRF Token Management Routes

Provides endpoints for CSRF token management.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.database import get_db
from api.dependencies import get_current_user
from models.user import User
from core.csrf import generate_csrf_token, store_csrf_token, delete_csrf_token
from core.config import get_settings
from core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()


@router.get("/csrf-token")
async def get_csrf_token_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get CSRF token for authenticated user.
    
    Returns a new CSRF token and sets it in a cookie.
    This endpoint should be called after login or when the token expires.
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    - csrf_token: CSRF token value
    - expires_in: Token expiration time in seconds
    
    **Response 401**: Not authenticated
    """
    # Generate new CSRF token
    csrf_token = generate_csrf_token()
    store_csrf_token(current_user.id, csrf_token, expires_in=3600)  # 1 hour
    
    # Create response
    response = JSONResponse(content={
        "csrf_token": csrf_token,
        "expires_in": 3600
    })
    
    # Set CSRF token in cookie with SameSite=Strict (not httpOnly so JS can read it)
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,  # Allow JavaScript to read it for X-CSRF-Token header
        samesite="strict",  # Prevent CSRF attacks
        secure=settings.ENVIRONMENT == "production",  # HTTPS only in production
        max_age=3600,  # 1 hour
        path="/"  # Available for all paths
    )
    
    return response


@router.post("/csrf-token/refresh")
async def refresh_csrf_token(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Refresh CSRF token.
    
    Generates a new CSRF token and invalidates the old one.
    Useful when token is about to expire or after suspicious activity.
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    - csrf_token: New CSRF token value
    - expires_in: Token expiration time in seconds
    
    **Response 401**: Not authenticated
    """
    # Delete old token
    delete_csrf_token(current_user.id)
    
    # Generate new CSRF token
    csrf_token = generate_csrf_token()
    store_csrf_token(current_user.id, csrf_token, expires_in=3600)  # 1 hour
    
    # Create response
    response = JSONResponse(content={
        "csrf_token": csrf_token,
        "expires_in": 3600
    })
    
    # Set CSRF token in cookie with SameSite=Strict (not httpOnly so JS can read it)
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,  # Allow JavaScript to read it for X-CSRF-Token header
        samesite="strict",  # Prevent CSRF attacks
        secure=settings.ENVIRONMENT == "production",  # HTTPS only in production
        max_age=3600,  # 1 hour
        path="/"  # Available for all paths
    )
    
    return response

