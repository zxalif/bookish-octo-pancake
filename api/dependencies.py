"""
API Dependencies

Shared dependencies for FastAPI routes.
Provides authentication, database session, and other common dependencies.
"""

from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import decode_access_token
from models.user import User
from models.subscription import Subscription
from services.auth_service import AuthService
from services.subscription_service import SubscriptionService

# HTTP Bearer token security scheme
security = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Dependency to get the current authenticated user from JWT token.
    
    This dependency:
    1. Extracts JWT token from Authorization header
    2. Decodes and validates the token
    3. Retrieves user from database
    4. Returns user object
    
    Args:
        credentials: HTTP Bearer credentials from Authorization header
        db: Database session
        
    Returns:
        User: Current authenticated user
        
    Raises:
        HTTPException: If token is invalid or user not found
    """
    token = credentials.credentials
    
    # Decode token
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get user_id from token
    user_id: str = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get user from database
    user = AuthService.get_user_by_id(user_id, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )
    
    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency to get current active user.
    
    This is an alias for get_current_user that explicitly checks for active status.
    The check is already done in get_current_user, but this provides clarity.
    
    Args:
        current_user: Current user from get_current_user dependency
        
    Returns:
        User: Current active user
    """
    return current_user


def get_optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    Optional dependency to get current user if authenticated.
    
    Unlike get_current_user, this doesn't raise an error if no token is provided.
    Useful for endpoints that work both with and without authentication.
    
    Args:
        credentials: Optional HTTP Bearer credentials
        db: Database session
        
    Returns:
        User: Current user if authenticated, None otherwise
    """
    if credentials is None:
        return None
    
    try:
        return get_current_user(credentials, db)
    except HTTPException:
        return None


def require_active_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Subscription:
    """
    Dependency to require an active subscription.
    
    For free tier launch, auto-creates free subscription if none exists.
    This ensures all users can access features during the 3-month free period.
    
    Args:
        current_user: Current authenticated user
        db: Database session
        
    Returns:
        Subscription: Active subscription (auto-created if needed)
        
    Raises:
        HTTPException: If subscription cannot be created or is expired
    """
    subscription = SubscriptionService.get_active_subscription(current_user.id, db)
    
    if not subscription:
        # Auto-create free subscription for new users (3-month free tier)
        subscription = SubscriptionService.create_free_subscription(current_user.id, db)
    
    return subscription

