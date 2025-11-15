"""
Authentication Routes

Handles user registration, login, and password reset.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
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
    background_tasks: BackgroundTasks,
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
        
        # Generate verification token
        verification_token = AuthService.generate_email_verification_token(user.id)
        
        # Commit user and subscription first
        db.commit()
        
        # Send verification email asynchronously (non-blocking)
        # This prevents blocking the registration response while waiting for SMTP
        background_tasks.add_task(
            EmailService.send_verification_email,
            user.email,
            user.id,
            verification_token
        )
        
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
        # Log failed login attempt (user doesn't exist or wrong password)
        # Hash email to prevent enumeration while still tracking
        import hashlib
        email_hash = hashlib.sha256(credentials.email.encode()).hexdigest()[:16]
        
        try:
            audit_log = UserAuditLog(
                user_id=None,  # No user for failed attempts
                action="login_failed",
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"Failed login attempt for email hash: {email_hash}, reason: incorrect_email_or_password"
            )
            db.add(audit_log)
            db.commit()
        except Exception as e:
            # Don't fail login if audit log fails
            logger.warning(f"Failed to create audit log for failed login: {str(e)}")
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if user is banned
    if user.is_banned:
        # Log failed login attempt (banned user)
        try:
            audit_log = UserAuditLog(
                user_id=user.id,
                action="login_failed",
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"Failed login attempt, reason: account_banned"
            )
            db.add(audit_log)
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to create audit log for banned login: {str(e)}")
        
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been banned. Please contact support for assistance.",
        )
    
    # Check if email is verified
    if not user.is_verified:
        # Log failed login attempt (email not verified)
        try:
            audit_log = UserAuditLog(
                user_id=user.id,
                action="login_failed",
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"Failed login attempt, reason: email_not_verified"
            )
            db.add(audit_log)
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to create audit log for unverified login: {str(e)}")
        
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
    
    # Generate CSRF token
    from core.csrf import generate_csrf_token, store_csrf_token
    from core.config import get_settings
    settings = get_settings()
    csrf_token = generate_csrf_token()
    store_csrf_token(user.id, csrf_token, expires_in=3600)  # 1 hour expiration
    
    # Create response with tokens
    token_data = AuthService.create_token_for_user(user)
    
    # Add CSRF token to response
    token_data["csrf_token"] = csrf_token
    
    # Create response with CSRF token in cookie
    from fastapi.responses import JSONResponse
    response = JSONResponse(content=token_data)
    
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


@router.get("/me", response_model=UserResponse, response_model_exclude_none=True)
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
    # SECURITY: Only include is_admin if user is actually an admin
    user_dict = current_user.to_dict()
    
    # Build response dict, excluding None values
    user_data = {
        "id": user_dict["id"],
        "email": user_dict["email"],
        "full_name": user_dict["full_name"],
    }
    
    # Only add is_admin if user is admin
    if current_user.is_admin:
        user_data["is_admin"] = True
    
    return user_data


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
    background_tasks: BackgroundTasks,
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
    
    # Create audit log entry if user exists (before async email send)
    if user:
        # Include verification status in audit log
        verification_status = "verified" if user.is_verified else "unverified"
        audit_log = UserAuditLog(
            user_id=user.id,
            action="forgot_password",
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"Password reset requested for email: {forgot_data.email}, user status: {verification_status}"
        )
        db.add(audit_log)
        db.commit()
    
    # Send reset email asynchronously (non-blocking)
    # This prevents blocking the API response while waiting for SMTP
    background_tasks.add_task(
        AuthService.send_password_reset_email,
        forgot_data.email,
        db
    )
    
    # Always return success immediately to prevent email enumeration
    # Note: Password reset works for both verified and unverified users
    # Unverified users can reset password but still need to verify email to login
    return {
        "message": "If an account with that email exists, a password reset link has been sent. Note: If your email is not verified, you will still need to verify it after resetting your password to log in."
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
        # Log failed password reset attempt
        try:
            # Hash token to prevent token enumeration while still tracking
            import hashlib
            token_hash = hashlib.sha256(reset_data.token.encode()).hexdigest()[:16]
            audit_log = UserAuditLog(
                user_id=None,  # No user for invalid tokens
                action="reset_password_failed",
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"Failed password reset attempt, token hash: {token_hash}, reason: invalid_or_expired_token"
            )
            db.add(audit_log)
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to create audit log for failed password reset: {str(e)}")
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token"
        )
    
    # Update password
    user.password_hash = get_password_hash(reset_data.new_password)
    
    # Create audit log entry for password reset
    verification_status = "verified" if user.is_verified else "unverified"
    audit_log = UserAuditLog(
        user_id=user.id,
        action="reset_password",
        ip_address=ip_address,
        user_agent=user_agent,
        details=f"Password reset completed successfully, user status: {verification_status}"
    )
    db.add(audit_log)
    db.commit()
    
    # Provide helpful message based on verification status
    if not user.is_verified:
        return {
            "message": "Password reset successfully. However, your email is not yet verified. Please verify your email address to log in. Check your inbox for the verification email, or use the resend verification feature.",
            "email_verified": False
        }
    
    return {
        "message": "Password reset successfully. You can now log in with your new password.",
        "email_verified": True
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
    # Get IP address for audit logging (before any checks)
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
    # First, get the user by user_id to check if they exist
    # SECURITY: Don't reveal if user exists or not - return same error as invalid token
    user_by_id = AuthService.get_user_by_id(verify_data.user_id, db)
    
    if not user_by_id:
        # Log failed verification attempt (user not found)
        try:
            audit_log = UserAuditLog(
                user_id=None,  # No user for invalid user_id
                action="verify_email_failed",
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"Failed email verification attempt, reason: user_not_found (user_id: {verify_data.user_id[:8]}...)"
            )
            db.add(audit_log)
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to create audit log for failed email verification: {str(e)}")
        
        # Return same error as invalid token to prevent user enumeration
        # Don't reveal if user exists or not
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Invalid or expired verification token. The link may have expired (24 hours) or already been used.",
                "error_code": "verification_token_expired",
                "suggestion": "Please request a new verification email. If you are logged in, use the 'Resend Verification Email' button, or use the /auth/resend-verification endpoint with your email address."
            }
        )
    
    # Check if already verified
    if user_by_id.is_verified:
        user_dict = user_by_id.to_dict()
        user_data = {
            "id": user_dict["id"],
            "email": user_dict["email"],
            "full_name": user_dict["full_name"],
        }
        if user_by_id.is_admin:
            user_data["is_admin"] = True
        return {
            "message": "Email already verified",
            "user": user_data
        }
    
    # Verify token and get user
    user = AuthService.verify_email_token(verify_data.token, db)
    
    if not user:
        # Log failed email verification attempt
        try:
            audit_log = UserAuditLog(
                user_id=verify_data.user_id,  # User ID from request
                action="verify_email_failed",
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"Failed email verification attempt, reason: invalid_or_expired_token"
            )
            db.add(audit_log)
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to create audit log for failed email verification: {str(e)}")
        
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
            detail={
                "message": "Invalid or expired verification token. The link may have expired (24 hours) or already been used.",
                "error_code": "verification_token_expired",
                "suggestion": "Please request a new verification email. If you are logged in, use the 'Resend Verification Email' button, or use the /auth/resend-verification endpoint with your email address."
            }
        )
    
    # Verify user_id matches (extra security check)
    if user.id != verify_data.user_id:
        # Log failed email verification attempt (token mismatch)
        try:
            audit_log = UserAuditLog(
                user_id=verify_data.user_id,  # User ID from request
                action="verify_email_failed",
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"Failed email verification attempt, reason: token_user_mismatch"
            )
            db.add(audit_log)
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to create audit log for failed email verification: {str(e)}")
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not match user"
        )
    
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
    
    user_dict = user.to_dict()
    user_data = {
        "id": user_dict["id"],
        "email": user_dict["email"],
        "full_name": user_dict["full_name"],
    }
    if user.is_admin:
        user_data["is_admin"] = True
    return {
        "message": "Email verified successfully. Welcome email sent!",
        "user": user_data
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
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Resend email verification link (unauthenticated).
    
    Sends a new verification email if the user exists and is not yet verified.
    Always returns success to prevent email enumeration attacks.
    
    **SECURITY**: Rate limited to 5 requests per minute per IP to prevent abuse.
    
    **Request Body**:
    - email: User's email address
    
    **Response 200**: Success message (always returns success for security)
    **Response 429**: Rate limit exceeded
    
    **Note**: If you are logged in, use `/auth/resend-verification-authenticated` instead.
    """
    # Get IP address from request for audit logging
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
    user = AuthService.get_user_by_email(resend_data.email, db)
    
    if user and not user.is_verified:
        # Generate new verification token
        verification_token = AuthService.generate_email_verification_token(user.id)
        
        # Create audit log entry for resend verification (before async email send)
        audit_log = UserAuditLog(
            user_id=user.id,
            action="resend_verification",
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"Verification email resent for: {resend_data.email}"
        )
        db.add(audit_log)
        db.commit()
        
        # Send verification email asynchronously (non-blocking)
        background_tasks.add_task(
            EmailService.send_verification_email,
            user.email,
            user.id,
            verification_token
        )
    
    # Always return success immediately to prevent email enumeration
    return {
        "message": "If an account with that email exists and is not verified, a verification email has been sent."
    }


@router.post("/resend-verification-authenticated", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def resend_verification_email_authenticated(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Resend email verification link (authenticated).
    
    Sends a new verification email to the currently authenticated user if not yet verified.
    No email address required - uses the logged-in user's email.
    
    **Authentication Required**: Yes (JWT token)
    **SECURITY**: Rate limited to 5 requests per minute per IP to prevent abuse.
    
    **Response 200**: Success message
    **Response 400**: User is already verified
    **Response 401**: Not authenticated
    **Response 429**: Rate limit exceeded
    """
    # Check if already verified
    if current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your email is already verified."
        )
    
    # Get IP address from request for audit logging
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
    # Generate new verification token
    verification_token = AuthService.generate_email_verification_token(current_user.id)
    
    # Create audit log entry (before async email send)
    audit_log = UserAuditLog(
        user_id=current_user.id,
        action="resend_verification",
        ip_address=ip_address,
        user_agent=user_agent,
        details=f"Verification email resent for authenticated user: {current_user.email}"
    )
    db.add(audit_log)
    db.commit()
    
    # Send verification email asynchronously (non-blocking)
    background_tasks.add_task(
        EmailService.send_verification_email,
        current_user.email,
        current_user.id,
        verification_token
    )
    
    return {
        "message": "A new verification email has been sent to your email address."
    }
