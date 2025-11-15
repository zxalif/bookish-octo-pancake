"""
User Routes

Handles user profile management and user-related operations.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from slowapi.util import get_remote_address

from core.database import get_db
from api.dependencies import get_current_user
from models.user import User
from models.user_audit_log import UserAuditLog
from services.auth_service import AuthService
from services.subscription_service import SubscriptionService

router = APIRouter()


# Request/Response Models
class UserUpdate(BaseModel):
    """
    User update request model.
    
    SECURITY: Email changes are NOT allowed via this endpoint.
    Email is a critical authentication identifier and should only be changed
    through a secure email verification flow (not yet implemented).
    """
    full_name: str | None = None
    email_notifications_enabled: bool | None = None
    # Email removed for security - email changes require verification flow
    
    class Config:
        json_schema_extra = {
            "example": {
                "full_name": "John Doe Updated",
                "email_notifications_enabled": True
            }
        }


class SubscriptionInfo(BaseModel):
    """Subscription information for user response."""
    id: str | None = None
    plan: str | None = None
    status: str | None = None
    billing_period: str | None = None
    current_period_start: str | None = None
    current_period_end: str | None = None
    cancel_at_period_end: bool = False
    last_billing_date: str | None = None
    next_billing_date: str | None = None
    last_billing_status: str | None = None
    trial_end_date: str | None = None


class UserResponse(BaseModel):
    """User response model.
    
    Optimized response containing only fields needed by frontend:
    - id: User identifier
    - email: User email address
    - full_name: User's display name
    - subscription: Active subscription information (if any)
    - is_admin: Admin status (ONLY included if user is admin, for security)
    - email_notifications_enabled: Whether user wants to receive email notifications
    
    Note: is_active, created_at, updated_at are excluded
    as they are not used by the frontend and are handled server-side.
    SECURITY: is_admin is only included if the user is actually an admin.
    SECURITY: is_verified is safe to include for authenticated users (they already know their own email).
    """
    id: str
    email: str
    full_name: str
    subscription: SubscriptionInfo | None = None
    is_admin: bool | None = None  # Optional - only set if user is admin
    email_notifications_enabled: bool = True
    is_verified: bool = False  # Email verification status (safe to include for authenticated users)


@router.get("/me", response_model=UserResponse, response_model_exclude_none=True)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get current user information with subscription data.
    
    Returns information about the currently authenticated user,
    including their active subscription if available.
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    - User information (id, email, full_name, etc.)
    - Subscription information (if active subscription exists)
    
    **Response 401**: Not authenticated
    """
    user_data = current_user.to_dict()
    
    # Get active subscription
    subscription = SubscriptionService.get_active_subscription(current_user.id, db)
    
    if subscription:
        subscription_data = subscription.to_dict()
        user_data["subscription"] = {
            "id": subscription_data.get("id"),
            "plan": subscription_data.get("plan"),
            "status": subscription_data.get("status"),
            "billing_period": subscription_data.get("billing_period"),
            "current_period_start": subscription_data.get("current_period_start"),
            "current_period_end": subscription_data.get("current_period_end"),
            "cancel_at_period_end": subscription_data.get("cancel_at_period_end", False),
            "last_billing_date": subscription_data.get("last_billing_date"),
            "next_billing_date": subscription_data.get("next_billing_date"),
            "last_billing_status": subscription_data.get("last_billing_status"),
            "trial_end_date": subscription_data.get("trial_end_date")
        }
    else:
        user_data["subscription"] = None
    
    # SECURITY: Only include is_admin if user is actually an admin
    # This prevents non-admin users from seeing the field at all
    if current_user.is_admin:
        user_data["is_admin"] = True
    # If not admin, don't include the field in the response dict
    
    # Build response, excluding None values (except subscription which can be None)
    response_data = {
        "id": user_data["id"],
        "email": user_data["email"],
        "full_name": user_data["full_name"],
        "subscription": user_data.get("subscription"),  # Can be None
        "email_notifications_enabled": current_user.email_notifications_enabled,
        "is_verified": current_user.is_verified,  # Safe to include for authenticated users
    }
    
    # Only add is_admin if it was set (user is admin)
    if "is_admin" in user_data:
        response_data["is_admin"] = user_data["is_admin"]
    
    return response_data


@router.put("/me", response_model=UserResponse)
async def update_current_user(
    request: Request,
    user_update: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update current user information.
    
    Updates the authenticated user's profile information.
    
    **SECURITY**: Email changes are NOT allowed via this endpoint.
    Email is a critical authentication identifier. To change email, a secure
    email verification flow must be implemented (separate endpoint with
    password confirmation and new email verification).
    
    **Authentication Required**: Yes (JWT token)
    
    **Request Body**:
    - full_name: Optional new full name
    
    **Response 200**:
    - Updated user information
    
    **Response 400**: Invalid input
    **Response 401**: Not authenticated
    """
    # Security check: Reject email updates (security vulnerability)
    # Email changes require:
    # 1. Password confirmation
    # 2. Email verification for new email
    # 3. Separate secure endpoint
    # This prevents account takeover attacks
    
    # Update full_name if provided
    if user_update.full_name is not None:
        # Security validations
        full_name = user_update.full_name.strip()
        
        # Validate full_name is not empty
        if not full_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Full name cannot be empty"
            )
        
        # Validate length (prevent DoS attacks)
        if len(full_name) > 255:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Full name must be 255 characters or less"
            )
        
        # Validate minimum length (prevent abuse)
        if len(full_name) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Full name must be at least 2 characters"
            )
        
        # Basic sanitization (remove excessive whitespace)
        full_name = ' '.join(full_name.split())
        
        old_full_name = current_user.full_name
        current_user.full_name = full_name
        
        # Create audit log entry for profile update
        ip_address = get_remote_address(request)
        user_agent = request.headers.get("user-agent", "")
        audit_log = UserAuditLog(
            user_id=current_user.id,
            action="update_profile",
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"Full name updated from '{old_full_name}' to '{full_name}'"
        )
        db.add(audit_log)
    
    # Update email_notifications_enabled if provided
    if user_update.email_notifications_enabled is not None:
        old_value = current_user.email_notifications_enabled
        current_user.email_notifications_enabled = user_update.email_notifications_enabled
        
        # Create audit log entry for notification preference change
        ip_address = get_remote_address(request)
        user_agent = request.headers.get("user-agent", "")
        audit_log = UserAuditLog(
            user_id=current_user.id,
            action="update_notification_preference",
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"Email notifications {'enabled' if user_update.email_notifications_enabled else 'disabled'} (was {'enabled' if old_value else 'disabled'})"
        )
        db.add(audit_log)
    
    # Validate that at least one field is being updated
    if user_update.full_name is None and user_update.email_notifications_enabled is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update"
        )
    
    db.commit()
    db.refresh(current_user)
    
    # Build response similar to get_current_user_info
    user_data = current_user.to_dict()
    subscription = SubscriptionService.get_active_subscription(current_user.id, db)
    
    if subscription:
        subscription_data = subscription.to_dict()
        user_data["subscription"] = {
            "id": subscription_data.get("id"),
            "plan": subscription_data.get("plan"),
            "status": subscription_data.get("status"),
            "billing_period": subscription_data.get("billing_period"),
            "current_period_start": subscription_data.get("current_period_start"),
            "current_period_end": subscription_data.get("current_period_end"),
            "cancel_at_period_end": subscription_data.get("cancel_at_period_end", False),
            "last_billing_date": subscription_data.get("last_billing_date"),
            "next_billing_date": subscription_data.get("next_billing_date"),
            "last_billing_status": subscription_data.get("last_billing_status"),
            "trial_end_date": subscription_data.get("trial_end_date")
        }
    else:
        user_data["subscription"] = None
    
    response_data = {
        "id": user_data["id"],
        "email": user_data["email"],
        "full_name": user_data["full_name"],
        "subscription": user_data.get("subscription"),
        "email_notifications_enabled": current_user.email_notifications_enabled,
        "is_verified": current_user.is_verified,  # Safe to include for authenticated users
    }
    
    if current_user.is_admin:
        response_data["is_admin"] = True
    
    return response_data


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_user(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete current user account.
    
    Deactivates the user account (soft delete).
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 204**: Account deactivated
    
    **Response 401**: Not authenticated
    """
    # Get IP address from request for audit logging
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
    # Create audit log entry before deletion
    audit_log = UserAuditLog(
        user_id=current_user.id,
        action="delete_account",
        ip_address=ip_address,
        user_agent=user_agent,
        details=f"Account deletion requested. Email: {current_user.email}, Account created: {current_user.created_at.isoformat() if current_user.created_at else 'unknown'}"
    )
    db.add(audit_log)
    
    # Soft delete: deactivate account
    current_user.is_active = False
    db.commit()
    
    return None
