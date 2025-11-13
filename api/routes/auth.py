"""
Authentication Routes

Handles user registration, login, and password reset.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from slowapi.util import get_remote_address
from datetime import datetime

from core.database import get_db
from core.logger import get_logger
from services.auth_service import AuthService, redis_client
from services.subscription_service import SubscriptionService
from services.email_service import EmailService
from api.dependencies import get_current_user
from models.user import User
from models.user_audit_log import UserAuditLog
from core.security import get_password_hash
from api.middleware.rate_limit import limiter

logger = get_logger(__name__)

router = APIRouter()
security = HTTPBearer()


# Request/Response Models
class UserRegister(BaseModel):
    """User registration request model."""
    email: EmailStr
    password: str
    full_name: str
    consent_data_processing: bool
    consent_marketing: bool
    consent_cookies: bool
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "securepassword123",
                "full_name": "John Doe",
                "consent_data_processing": True,
                "consent_marketing": False,
                "consent_cookies": True
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
@limiter.limit("5/minute")
async def register(
    request: Request,
    user_data: UserRegister,
    db: Session = Depends(get_db)
):
    """
    Register a new user.
    
    Creates a new user account, sends verification email, and returns an access token.
    Note: User must verify their email before full access is granted.
    
    **SECURITY**: Rate limited to 5 requests per minute per IP to prevent abuse.
    
    **Request Body**:
    - email: User's email address (must be unique)
    - password: Plain text password (will be hashed)
    - full_name: User's full name
    
    **Response 201**:
    - access_token: JWT token for authentication
    - token_type: "bearer"
    - user: User information (is_verified will be False until email is verified)
    
    **Response 400**: Email already registered
    **Response 429**: Rate limit exceeded
    """
    try:
        # Get IP address from request
        ip_address = get_remote_address(request)
        user_agent = request.headers.get("user-agent", "")
        
        # Validate required consents (GDPR/CCPA compliance)
        if not user_data.consent_data_processing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Data processing consent is required to create an account"
            )
        if not user_data.consent_cookies:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cookie consent is required to use our service"
            )
        
        # Register user with consent and IP
        now = datetime.utcnow()
        user = AuthService.register_user(
            email=user_data.email,
            password=user_data.password,
            full_name=user_data.full_name,
            consent_data_processing=user_data.consent_data_processing,
            consent_marketing=user_data.consent_marketing,
            consent_cookies=user_data.consent_cookies,
            registration_ip=ip_address,
            db=db
        )
        
        # Create audit log entry for registration
        audit_log = UserAuditLog(
            user_id=user.id,
            action="register",
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"User registered with consents: data_processing={user_data.consent_data_processing}, marketing={user_data.consent_marketing}, cookies={user_data.consent_cookies}"
        )
        db.add(audit_log)
        
        # Auto-create free subscription for new user (1-month free tier)
        SubscriptionService.create_free_subscription(user.id, db)
        
        # Generate verification token and send verification email
        verification_token = AuthService.generate_email_verification_token(user.id)
        await EmailService.send_verification_email(user.email, user.id, verification_token)
        
        db.commit()
        
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
@limiter.limit("10/minute")
async def login(
    request: Request,
    credentials: UserLogin,
    db: Session = Depends(get_db)
):
    """
    User login.
    
    Authenticates user with email and password, returns access token.
    Requires email verification for access.
    
    **SECURITY**: Rate limited to 10 requests per minute per IP to prevent brute force attacks.
    
    **Request Body**:
    - email: User's email address
    - password: User's password
    
    **Response 200**:
    - access_token: JWT token for authentication
    - token_type: "bearer"
    - user: User information
    
    **Response 401**: Invalid credentials or email not verified
    **Response 429**: Rate limit exceeded
    """
    # Get IP address from request
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
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
    
    # Update last login IP and create audit log
    user.last_login_ip = ip_address
    audit_log = UserAuditLog(
        user_id=user.id,
        action="login",
        ip_address=ip_address,
        user_agent=user_agent,
        details="User logged in successfully"
    )
    db.add(audit_log)
    db.commit()
    
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


class ForgotPasswordRequest(BaseModel):
    """Forgot password request model."""
    email: EmailStr
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com"
            }
        }


@router.post("/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    forgot_data: ForgotPasswordRequest,
    db: Session = Depends(get_db)
):
    """
    Request password reset.
    
    Sends a password reset email to the user if the email exists.
    Always returns success to prevent email enumeration attacks.
    
    **SECURITY**: Rate limited to 5 requests per minute per IP to prevent abuse.
    
    **Request Body**:
    - email: User's email address
    
    **Response 200**: Success message (always returns success for security)
    **Response 429**: Rate limit exceeded
    
    **Note**: Requires SMTP configuration in environment variables.
    """
    # Get IP address from request for audit logging
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
    # Get user if exists (for audit logging)
    user = AuthService.get_user_by_email(forgot_data.email, db)
    
    # Send reset email if user exists (but don't reveal if they don't)
    email_sent = await AuthService.send_password_reset_email(forgot_data.email, db)
    
    # Create audit log entry if user exists
    if user:
        audit_log = UserAuditLog(
            user_id=user.id,
            action="forgot_password",
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"Password reset requested for email: {forgot_data.email}"
        )
        db.add(audit_log)
        db.commit()
    
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
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    reset_data: PasswordResetRequest,
    db: Session = Depends(get_db)
):
    """
    Reset password with reset token.
    
    Validates the reset token and updates the user's password.
    Token must be used within 1 hour and can only be used once.
    
    **SECURITY**: Rate limited to 5 requests per minute per IP to prevent abuse.
    
    **Request Body**:
    - token: Password reset token from email
    - new_password: New password
    
    **Response 200**: Success message
    
    **Response 400**: Invalid or expired token
    **Response 429**: Rate limit exceeded
    """
    # Get IP address from request for audit logging
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
    # Verify token and get user
    user = AuthService.verify_password_reset_token(reset_data.token, db)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token"
        )
    
    # Update password
    user.password_hash = get_password_hash(reset_data.new_password)
    
    # Create audit log entry for password reset
    audit_log = UserAuditLog(
        user_id=user.id,
        action="reset_password",
        ip_address=ip_address,
        user_agent=user_agent,
        details="Password reset completed successfully"
    )
    db.add(audit_log)
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
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    verify_data: VerifyEmailRequest,
    db: Session = Depends(get_db)
):
    """
    Verify user email address.
    
    Validates the verification token and marks the user's email as verified.
    Sends a welcome email after successful verification.
    
    **SECURITY**: Rate limited to 10 requests per minute per IP to prevent abuse.
    
    **Request Body**:
    - token: Email verification token from email
    - user_id: User UUID from email
    
    **Response 200**: Success message
    
    **Response 400**: Invalid or expired token
    **Response 429**: Rate limit exceeded
    """
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
    
    # Get IP address from request for audit logging
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
    # Mark email as verified
    user.is_verified = True
    
    # Create audit log entry for email verification
    audit_log = UserAuditLog(
        user_id=user.id,
        action="verify_email",
        ip_address=ip_address,
        user_agent=user_agent,
        details=f"Email verified successfully: {user.email}"
    )
    db.add(audit_log)
    
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


class ResendVerificationRequest(BaseModel):
    """Resend verification email request model."""
    email: EmailStr
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com"
            }
        }


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def resend_verification_email(
    request: Request,
    resend_data: ResendVerificationRequest,
    db: Session = Depends(get_db)
):
    """
    Resend email verification link.
    
    Sends a new verification email if the user exists and is not yet verified.
    Always returns success to prevent email enumeration attacks.
    
    **SECURITY**: Rate limited to 5 requests per minute per IP to prevent abuse.
    
    **Request Body**:
    - email: User's email address
    
    **Response 200**: Success message (always returns success for security)
    **Response 429**: Rate limit exceeded
    """
    # Get IP address from request for audit logging
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
    user = AuthService.get_user_by_email(resend_data.email, db)
    
    if user and not user.is_verified:
        # Generate new verification token and send email
        verification_token = AuthService.generate_email_verification_token(user.id)
        await EmailService.send_verification_email(user.email, user.id, verification_token)
        
        # Create audit log entry for resend verification
        audit_log = UserAuditLog(
            user_id=user.id,
            action="resend_verification",
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"Verification email resent for: {resend_data.email}"
        )
        db.add(audit_log)
        db.commit()
    
    # Always return success to prevent email enumeration
    return {
        "message": "If an account with that email exists and is not verified, a verification email has been sent."
    }
