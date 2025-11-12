"""
Subscription Routes

Handles subscription management.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from api.dependencies import get_current_user
from models.user import User
from models.subscription import Subscription, SubscriptionPlan
from services.subscription_service import SubscriptionService

router = APIRouter()


# Request/Response Models
class SubscriptionResponse(BaseModel):
    """Subscription response model."""
    id: str
    user_id: str
    plan: str
    status: str
    paddle_subscription_id: str | None
    current_period_start: str | None
    current_period_end: str | None
    cancel_at_period_end: bool
    created_at: str
    updated_at: str


class SubscriptionCreate(BaseModel):
    """Subscription creation request model."""
    plan: str  # starter, professional, power
    billing_period: str = "monthly"  # monthly or yearly
    
    class Config:
        json_schema_extra = {
            "example": {
                "plan": "professional",
                "billing_period": "monthly"
            }
        }


@router.get("/current", response_model=SubscriptionResponse)
async def get_current_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user's current active subscription.
    
    Auto-creates free subscription if none exists (3-month free tier).
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    - Current subscription information (auto-created if needed)
    
    **Response 401**: Not authenticated
    """
    subscription = SubscriptionService.get_active_subscription(current_user.id, db)
    
    if not subscription:
        # Auto-create free subscription for new users (3-month free tier)
        subscription = SubscriptionService.create_free_subscription(current_user.id, db)
    
    return subscription.to_dict()


@router.get("/history", response_model=list[SubscriptionResponse])
async def get_subscription_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user's subscription history.
    
    Returns all subscriptions (active and past) for the user.
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    - List of all user subscriptions
    """
    subscriptions = db.query(Subscription).filter(
        Subscription.user_id == current_user.id
    ).order_by(Subscription.created_at.desc()).all()
    
    return [sub.to_dict() for sub in subscriptions]


@router.post("/create", response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    subscription_data: SubscriptionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new subscription.
    
    For FREE plan, no payment required (auto-creates 3-month free tier).
    For paid plans, this should be called after payment is processed.
    
    **Authentication Required**: Yes (JWT token)
    
    **Request Body**:
    - plan: Subscription plan (free, starter, professional, power)
    - billing_period: Billing period (monthly, yearly) - not used for free plan
    
    **Response 201**:
    - Created subscription information
    
    **Response 400**: Invalid plan or user already has active subscription (for paid plans)
    **Response 401**: Not authenticated
    """
    try:
        # For free plan, use create_free_subscription
        if subscription_data.plan == "free":
            subscription = SubscriptionService.create_free_subscription(
                user_id=current_user.id,
                db=db
            )
        else:
            # For paid plans, use create_subscription
            subscription = SubscriptionService.create_subscription(
                user_id=current_user.id,
                plan=subscription_data.plan,
                billing_period=subscription_data.billing_period,
                db=db
            )
        
        return subscription.to_dict()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/cancel")
async def cancel_subscription(
    cancel_at_period_end: bool = True,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Cancel current subscription.
    
    **Authentication Required**: Yes (JWT token)
    
    **Query Parameters**:
    - cancel_at_period_end: If True, cancel at period end; if False, cancel immediately
    
    **Response 200**:
    - Cancelled subscription information
    
    **Response 404**: No active subscription found
    **Response 401**: Not authenticated
    """
    subscription = SubscriptionService.get_active_subscription(current_user.id, db)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription found"
        )
    
    cancelled = SubscriptionService.cancel_subscription(
        subscription_id=subscription.id,
        user_id=current_user.id,
        cancel_at_period_end=cancel_at_period_end,
        db=db
    )
    
    return {
        "message": "Subscription cancelled successfully",
        "subscription": cancelled.to_dict()
    }


@router.get("/limits")
async def get_subscription_limits(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get subscription plan limits.
    
    Returns the limits for the user's current plan.
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    - Plan limits for current subscription
    
    **Response 404**: No active subscription found
    """
    subscription = SubscriptionService.get_active_subscription(current_user.id, db)
    
    if not subscription:
        # Auto-create free subscription for new users (3-month free tier)
        subscription = SubscriptionService.create_free_subscription(current_user.id, db)
    
    limits = SubscriptionService.get_plan_limits(subscription.plan.value)
    
    return {
        "plan": subscription.plan.value,
        "limits": limits
    }
