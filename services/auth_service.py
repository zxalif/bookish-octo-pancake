"""
Authentication Service

Handles user authentication business logic:
- User registration
- User login
- Password verification
- Token generation
"""

from typing import Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from datetime import datetime, timedelta
import secrets
import redis

from models.user import User
from core.security import verify_password, get_password_hash, create_access_token, create_refresh_token, decode_refresh_token
from core.config import get_settings
from core.logger import get_logger
from services.email_service import EmailService

settings = get_settings()
logger = get_logger(__name__)

# Redis client for password reset tokens
try:
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
except Exception:
    redis_client = None  # Fallback if Redis not available


class AuthService:
    """Service for handling authentication operations."""
    
    @staticmethod
    def register_user(
        email: str,
        password: str,
        full_name: str,
        consent_data_processing: bool,
        consent_marketing: bool,
        consent_cookies: bool,
        registration_ip: str | None = None,
        db: Session = None
    ) -> User:
        """
        Register a new user with consent tracking.
        
        Args:
            email: User's email address
            password: Plain text password
            full_name: User's full name
            consent_data_processing: GDPR consent for data processing
            consent_marketing: Consent for marketing emails
            consent_cookies: Consent for cookies
            registration_ip: IP address from registration
            db: Database session
            
        Returns:
            User: Created user object
            
        Raises:
            HTTPException: If email already exists
        """
        from fastapi import HTTPException, status
        from datetime import datetime
        
        # Check if user already exists
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        now = datetime.utcnow()
        
        # Create new user with consent tracking
        user = User(
            email=email,
            password_hash=get_password_hash(password),
            full_name=full_name,
            is_active=True,
            is_verified=False,  # Email verification required
            # Consent tracking
            consent_data_processing=consent_data_processing,
            consent_marketing=consent_marketing,
            consent_cookies=consent_cookies,
            consent_data_processing_at=now if consent_data_processing else None,
            consent_marketing_at=now if consent_marketing else None,
            consent_cookies_at=now if consent_cookies else None,
            # IP tracking
            registration_ip=registration_ip
        )
        
        db.add(user)
        db.flush()  # Flush to get user.id without committing
        
        return user
    
    @staticmethod
    def authenticate_user(
        email: str,
        password: str,
        db: Session
    ) -> Optional[User]:
        """
        Authenticate a user with email and password.
        
        Args:
            email: User's email address
            password: Plain text password
            db: Database session
            
        Returns:
            User: Authenticated user if credentials are valid, None otherwise
        """
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            return None
        
        if not verify_password(password, user.password_hash):
            return None
        
        if not user.is_active:
            return None
        
        return user
    
    @staticmethod
    def create_token_for_user(user: User) -> dict:
        """
        Create access token and refresh token for authenticated user.
        
        Args:
            user: User object
            
        Returns:
            dict: Token data with access_token, refresh_token, and token_type
        """
        access_token = create_access_token(data={"sub": user.id})
        refresh_token = create_refresh_token(data={"sub": user.id})
        
        # Store refresh token in Redis
        if redis_client:
            key = f"refresh_token:{user.id}"
            # Store with expiration matching token expiration (30 days)
            redis_client.setex(
                key, 
                settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60, 
                refresh_token
            )
        
        # Build user dict - only include is_admin if user is admin
        user_dict = user.to_dict()
        
        # Build response dict, excluding None values
        response_user = {
            "id": user_dict["id"],
            "email": user_dict["email"],
            "full_name": user_dict["full_name"],
        }
        
        # SECURITY: Only include is_admin if user is actually an admin
        if user.is_admin:
            response_user["is_admin"] = True
        # If not admin, don't include the field in the response
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": response_user
        }
    
    @staticmethod
    def refresh_access_token(refresh_token: str, db: Session) -> dict:
        """
        Refresh access token using refresh token.
        
        Args:
            refresh_token: Refresh token string
            db: Database session
            
        Returns:
            dict: New token data with access_token, refresh_token, and token_type
            
        Raises:
            HTTPException: If refresh token is invalid or expired
        """
        # Decode refresh token
        payload = decode_refresh_token(refresh_token)
        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Get user_id from token
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token payload",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Verify refresh token is stored in Redis (not revoked)
        if redis_client:
            key = f"refresh_token:{user_id}"
            stored_token = redis_client.get(key)
            if stored_token != refresh_token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Refresh token has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        
        # Get user from database
        user = AuthService.get_user_by_id(user_id, db)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Generate new tokens
        return AuthService.create_token_for_user(user)
    
    @staticmethod
    def revoke_refresh_token(user_id: str) -> bool:
        """
        Revoke a refresh token by removing it from Redis.
        
        Args:
            user_id: User UUID
            
        Returns:
            bool: True if token was revoked, False otherwise
        """
        if redis_client:
            key = f"refresh_token:{user_id}"
            return bool(redis_client.delete(key))
        return False
    
    @staticmethod
    def login_user(
        email: str,
        password: str,
        db: Session
    ) -> dict:
        """
        Login user and return token.
        
        Args:
            email: User's email address
            password: Plain text password
            db: Database session
            
        Returns:
            dict: Token data with access_token and user info
            
        Raises:
            HTTPException: If credentials are invalid
        """
        user = AuthService.authenticate_user(email, password, db)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return AuthService.create_token_for_user(user)
    
    @staticmethod
    def get_user_by_id(user_id: str, db: Session) -> Optional[User]:
        """
        Get user by ID.
        
        Args:
            user_id: User UUID
            db: Database session
            
        Returns:
            User: User object if found, None otherwise
        """
        return db.query(User).filter(User.id == user_id).first()
    
    @staticmethod
    def get_user_by_email(email: str, db: Session) -> Optional[User]:
        """
        Get user by email.
        
        Args:
            email: User's email address
            db: Database session
            
        Returns:
            User: User object if found, None otherwise
        """
        return db.query(User).filter(User.email == email).first()
    
    @staticmethod
    def generate_password_reset_token(user_id: str) -> str:
        """
        Generate a password reset token and store it in Redis.
        
        Args:
            user_id: User UUID
            
        Returns:
            str: Password reset token
        """
        token = secrets.token_urlsafe(32)
        
        # Store token in Redis with 1 hour expiration
        if redis_client:
            key = f"password_reset:{token}"
            redis_client.setex(key, 3600, user_id)  # 1 hour = 3600 seconds
        # If Redis not available, token won't be validated (fallback for development)
        
        return token
    
    @staticmethod
    def verify_password_reset_token(token: str, db: Session) -> Optional[User]:
        """
        Verify password reset token and return user.
        
        Args:
            token: Password reset token
            db: Database session
            
        Returns:
            User: User object if token is valid, None otherwise
        """
        if not redis_client:
            # Redis not available - return None (token validation disabled)
            return None
        
        # Get user_id from Redis
        key = f"password_reset:{token}"
        user_id = redis_client.get(key)
        
        if not user_id:
            return None  # Token not found or expired
        
        # Get user from database
        user = AuthService.get_user_by_id(user_id, db)
        
        if user:
            # Delete token after use (one-time use)
            redis_client.delete(key)
        
        return user
    
    @staticmethod
    async def send_password_reset_email(email: str, db: Session) -> bool:
        """
        Send password reset email.
        
        Args:
            email: User's email address
            db: Database session
            
        Returns:
            bool: True if email sent successfully
        """
        user = AuthService.get_user_by_email(email, db)
        if not user:
            # Don't reveal if user exists (security)
            return True
        
        # Generate reset token
        token = AuthService.generate_password_reset_token(user.id)
        
        # Send email with token
        return await EmailService.send_password_reset_email(email, db, token=token)
    
    @staticmethod
    def generate_email_verification_token(user_id: str) -> str:
        """
        Generate an email verification token and store it in Redis.
        
        Args:
            user_id: User UUID
            
        Returns:
            str: Email verification token
        """
        token = secrets.token_urlsafe(32)
        
        # Store token in Redis with 24 hour expiration
        if redis_client:
            key = f"email_verification:{token}"
            redis_client.setex(key, 24 * 60 * 60, user_id)  # 24 hours = 86400 seconds
        # If Redis not available, token won't be validated (fallback for development)
        
        return token
    
    @staticmethod
    def verify_email_token(token: str, db: Session) -> Optional[User]:
        """
        Verify email verification token and return user.
        
        Args:
            token: Email verification token
            db: Database session
            
        Returns:
            User: User object if token is valid, None otherwise
        """
        if not redis_client:
            # Redis not available - log warning and return None
            logger.warning("Redis not available - email verification token validation disabled")
            return None
        
        # Get user_id from Redis
        key = f"email_verification:{token}"
        try:
            user_id = redis_client.get(key)
        except Exception as e:
            logger.error(f"Error reading from Redis: {str(e)}", exc_info=True)
            return None
        
        if not user_id:
            logger.warning(f"Token not found in Redis: {key}")
            return None  # Token not found or expired
        
        # Get user from database
        user = AuthService.get_user_by_id(user_id, db)
        
        if user:
            # Delete token after use (one-time use)
            try:
                redis_client.delete(key)
            except Exception as e:
                logger.warning(f"Error deleting token from Redis: {str(e)}", exc_info=True)
        
        return user

