"""
User Routes

Handles user profile management and user-related operations.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from core.database import get_db
from api.dependencies import get_current_user
from models.user import User
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
    # Email removed for security - email changes require verification flow
    
    class Config:
        json_schema_extra = {
            "example": {
                "full_name": "John Doe Updated"
            }
        }


class SubscriptionInfo(BaseModel):
    """Subscription information for user response."""
    id: str | None = None
    plan: str | None = None
    status: str | None = None
    current_period_start: str | None = None
    current_period_end: str | None = None
    cancel_at_period_end: bool = False


class UserResponse(BaseModel):
    """User response model."""
    id: str
    email: str
    full_name: str
    is_active: bool
    is_verified: bool
    created_at: str
    updated_at: str
    subscription: SubscriptionInfo | None = None


@router.get("/me", response_model=UserResponse)
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
            "current_period_start": subscription_data.get("current_period_start"),
            "current_period_end": subscription_data.get("current_period_end"),
            "cancel_at_period_end": subscription_data.get("cancel_at_period_end", False)
        }
    else:
        user_data["subscription"] = None
    
    return user_data


@router.put("/me", response_model=UserResponse)
async def update_current_user(
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
        
        current_user.full_name = full_name
    
    # Validate that at least one field is being updated
    if user_update.full_name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update"
        )
    
    db.commit()
    db.refresh(current_user)
    
    return current_user.to_dict()


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_user(
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
    current_user.is_active = False
    db.commit()
    
    return None
