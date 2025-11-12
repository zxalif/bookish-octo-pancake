"""
Authentication Routes

Handles user registration, login, and password reset.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from core.database import get_db
from core.logger import get_logger
from services.auth_service import AuthService
from services.subscription_service import SubscriptionService
from api.dependencies import get_current_user
from models.user import User
from core.security import get_password_hash

logger = get_logger(__name__)

router = APIRouter()
security = HTTPBearer()


# Request/Response Models
class UserRegister(BaseModel):
    """User registration request model."""
    email: EmailStr
    password: str
    full_name: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "securepassword123",
                "full_name": "John Doe"
            }
        }


class UserLogin(BaseModel):
    """User login request model."""
    email: EmailStr
    password: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "securepassword123"
            }
        }


class TokenResponse(BaseModel):
    """Token response model."""
    access_token: str
    refresh_token: str
    token_type: str
    user: dict


class UserResponse(BaseModel):
    """User response model."""
    id: str
    email: str
    full_name: str
    is_active: bool
    is_verified: bool
    created_at: str
    updated_at: str


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserRegister,
    db: Session = Depends(get_db)
):
    """
    Register a new user.
    
    Creates a new user account, sends verification email, and returns an access token.
    Note: User must verify their email before full access is granted.
    
    **Request Body**:
    - email: User's email address (must be unique)
    - password: Plain text password (will be hashed)
    - full_name: User's full name
    
    **Response 201**:
    - access_token: JWT token for authentication
    - token_type: "bearer"
    - user: User information (is_verified will be False until email is verified)
    
    **Response 400**: Email already registered
    """
    try:
        user = AuthService.register_user(
            email=user_data.email,
            password=user_data.password,
            full_name=user_data.full_name,
            db=db
        )
        
        # Auto-create free subscription for new user (1-month free tier)
        SubscriptionService.create_free_subscription(user.id, db)
        
        # Generate verification token and send verification email
        from services.email_service import EmailService
        verification_token = AuthService.generate_email_verification_token(user.id)
        await EmailService.send_verification_email(user.email, user.id, verification_token)
        
        token_data = AuthService.create_token_for_user(user)
        
        return token_data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration failed: {str(e)}"
        )


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: UserLogin,
    db: Session = Depends(get_db)
):
    """
    User login.
    
    Authenticates user with email and password, returns access token.
    Requires email verification for access.
    
    **Request Body**:
    - email: User's email address
    - password: User's password
    
    **Response 200**:
    - access_token: JWT token for authentication
    - token_type: "bearer"
    - user: User information
    
    **Response 401**: Invalid credentials or email not verified
    """
    user = AuthService.authenticate_user(
        email=credentials.email,
        password=credentials.password,
        db=db
    )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if email is verified
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please check your email and verify your account before logging in.",
        )
    
    return AuthService.create_token_for_user(user)


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """
    Get current user information.
    
    Returns information about the currently authenticated user.
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    - User information (id, email, full_name, etc.)
    
    **Response 401**: Not authenticated
    """
    return current_user.to_dict()


@router.post("/forgot-password")
async def forgot_password(
    email: EmailStr,
    db: Session = Depends(get_db)
):
    """
    Request password reset.
    
    Sends a password reset email to the user if the email exists.
    Always returns success to prevent email enumeration attacks.
    
    **Request Body**:
    - email: User's email address
    
    **Response 200**: Success message (always returns success for security)
    
    **Note**: Requires SMTP configuration in environment variables.
    """
    # Send reset email if user exists (but don't reveal if they don't)
    await AuthService.send_password_reset_email(email, db)
    
    # Always return success to prevent email enumeration
    return {
        "message": "If an account with that email exists, a password reset link has been sent."
    }


class PasswordResetRequest(BaseModel):
    """Password reset request model."""
    token: str
    new_password: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "token": "reset-token-from-email",
                "new_password": "newsecurepassword123"
            }
        }


@router.post("/reset-password")
async def reset_password(
    reset_data: PasswordResetRequest,
    db: Session = Depends(get_db)
):
    """
    Reset password with reset token.
    
    Validates the reset token and updates the user's password.
    Token must be used within 1 hour and can only be used once.
    
    **Request Body**:
    - token: Password reset token from email
    - new_password: New password
    
    **Response 200**: Success message
    
    **Response 400**: Invalid or expired token
    """
    # Verify token and get user
    user = AuthService.verify_password_reset_token(reset_data.token, db)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token"
        )
    
    # Update password
    user.password_hash = get_password_hash(reset_data.new_password)
    db.commit()
    
    return {
        "message": "Password reset successfully"
    }


class RefreshTokenRequest(BaseModel):
    """Refresh token request model."""
    refresh_token: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
            }
        }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    refresh_data: RefreshTokenRequest,
    db: Session = Depends(get_db)
):
    """
    Refresh access token.
    
    Generates a new access token and refresh token using a valid refresh token.
    The old refresh token is invalidated and a new one is issued.
    
    **Request Body**:
    - refresh_token: Valid refresh token
    
    **Response 200**:
    - access_token: New JWT access token
    - refresh_token: New JWT refresh token
    - token_type: "bearer"
    - user: User information
    
    **Response 401**: Invalid or expired refresh token
    """
    try:
        return AuthService.refresh_access_token(
            refresh_token=refresh_data.refresh_token,
            db=db
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token refresh failed: {str(e)}"
        )


class VerifyEmailRequest(BaseModel):
    """Email verification request model."""
    token: str
    user_id: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "token": "verification-token-from-email",
                "user_id": "user-uuid"
            }
        }


@router.post("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(
    verify_data: VerifyEmailRequest,
    db: Session = Depends(get_db)
):
    """
    Verify user email address.
    
    Validates the verification token and marks the user's email as verified.
    Sends a welcome email after successful verification.
    
    **Request Body**:
    - token: Email verification token from email
    - user_id: User UUID from email
    
    **Response 200**: Success message
    
    **Response 400**: Invalid or expired token
    """
    from services.email_service import EmailService
    from services.subscription_service import SubscriptionService
    
    # First, get the user by user_id to check if they exist
    user_by_id = AuthService.get_user_by_id(verify_data.user_id, db)
    
    if not user_by_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check if already verified
    if user_by_id.is_verified:
        return {
            "message": "Email already verified",
            "user": user_by_id.to_dict()
        }
    
    # Verify token and get user
    user = AuthService.verify_email_token(verify_data.token, db)
    
    if not user:
        # Check if Redis is available
        from services.auth_service import redis_client
        if not redis_client:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Email verification service is temporarily unavailable. Please try again later or contact support."
            )
        
        # Token is invalid/expired, but user exists and is not verified
        # Provide helpful error message with resend option
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or expired verification token. The link may have expired (24 hours) or already been used. Please request a new verification email by logging in or using the resend verification feature."
        )
    
    # Verify user_id matches (extra security check)
    if user.id != verify_data.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not match user"
        )
    
    # Mark email as verified
    user.is_verified = True
    db.commit()
    db.refresh(user)
    
    # Get user's subscription plan name for welcome email
    active_subscription = user.get_active_subscription()
    plan_name = "Free"
    if active_subscription:
        # plan is an enum, use .value to get the string value
        plan_name = active_subscription.plan.value.replace("_", " ").title()
    
    # Send welcome email (non-blocking, don't fail verification if email fails)
    try:
        email_sent = await EmailService.send_welcome_email(
            email=user.email,
            full_name=user.full_name,
            plan_name=plan_name
        )
        if not email_sent:
            logger.warning(f"Welcome email failed to send to {user.email}, but verification succeeded")
    except Exception as e:
        logger.error(f"Error sending welcome email to {user.email}: {str(e)}", exc_info=True)
        # Don't fail verification if email sending fails
    
    return {
        "message": "Email verified successfully. Welcome email sent!",
        "user": user.to_dict()
    }


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
async def resend_verification_email(
    email: EmailStr,
    db: Session = Depends(get_db)
):
    """
    Resend email verification link.
    
    Sends a new verification email if the user exists and is not yet verified.
    Always returns success to prevent email enumeration attacks.
    
    **Request Body**:
    - email: User's email address
    
    **Response 200**: Success message (always returns success for security)
    """
    from services.email_service import EmailService
    
    user = AuthService.get_user_by_email(email, db)
    
    if user and not user.is_verified:
        # Generate new verification token and send email
        verification_token = AuthService.generate_email_verification_token(user.id)
        await EmailService.send_verification_email(user.email, user.id, verification_token)
    
    # Always return success to prevent email enumeration
    return {
        "message": "If an account with that email exists and is not verified, a verification email has been sent."
    }
