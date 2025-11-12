"""
Security Utilities

JWT token generation/validation and password hashing.
"""

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
import hashlib

from core.config import get_settings

settings = get_settings()

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _prehash_password(password: str) -> str:
    """
    Pre-hash password with SHA-256 to handle bcrypt's 72-byte limit.
    
    This prevents security issues where passwords longer than 72 bytes
    would be truncated, causing collisions. By hashing first, all passwords
    are normalized to 32 bytes (SHA-256 output) before being passed to bcrypt.
    
    Args:
        password: Plain text password (any length)
        
    Returns:
        str: SHA-256 hash of password (64 hex characters = 32 bytes)
    """
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against a hashed password.
    
    Uses SHA-256 pre-hashing to handle passwords longer than 72 bytes securely.
    
    Args:
        plain_password: Plain text password (any length)
        hashed_password: Hashed password from database
        
    Returns:
        bool: True if password matches, False otherwise
    """
    # Pre-hash the password with SHA-256 before verification
    prehashed = _prehash_password(plain_password)
    return pwd_context.verify(prehashed, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Hash a password using SHA-256 + bcrypt for secure handling of long passwords.
    
    SECURITY: This function pre-hashes passwords with SHA-256 before passing
    to bcrypt. This prevents the bcrypt 72-byte limit from causing security issues
    where passwords longer than 72 bytes would be truncated and could collide.
    
    Process:
    1. Hash password with SHA-256 (produces fixed 32-byte output)
    2. Pass SHA-256 hash to bcrypt (which has 72-byte limit, so 32 bytes is safe)
    
    This ensures:
    - Passwords of any length are supported
    - No password truncation occurs
    - No collisions from truncation
    - Full bcrypt security is maintained
    
    Args:
        password: Plain text password (any length)
        
    Returns:
        str: Bcrypt hash of SHA-256 pre-hashed password
    """
    # Pre-hash with SHA-256 to handle bcrypt's 72-byte limit
    prehashed = _prehash_password(password)
    return pwd_context.hash(prehashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.
    
    Args:
        data: Data to encode in the token (usually {"sub": user_id})
        expires_delta: Optional expiration time delta
        
    Returns:
        str: Encoded JWT token
        
    Example:
        ```python
        token = create_access_token({"sub": str(user.id)})
        ```
    """
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """
    Decode and validate a JWT access token.
    
    Args:
        token: JWT token string
        
    Returns:
        dict: Decoded token payload if valid, None otherwise
        
    Example:
        ```python
        payload = decode_access_token(token)
        if payload:
            user_id = payload.get("sub")
        ```
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None


def create_refresh_token(data: dict) -> str:
    """
    Create a JWT refresh token with longer expiration.
    
    Args:
        data: Data to encode in the token (usually {"sub": user_id})
        
    Returns:
        str: Encoded JWT refresh token
        
    Example:
        ```python
        refresh_token = create_refresh_token({"sub": str(user.id)})
        ```
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_refresh_token(token: str) -> Optional[dict]:
    """
    Decode and validate a JWT refresh token.
    
    Args:
        token: JWT refresh token string
        
    Returns:
        dict: Decoded token payload if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        # Verify it's a refresh token
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None
