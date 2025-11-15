"""
Subscription Routes

Handles subscription management.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from slowapi.util import get_remote_address

from core.database import get_db
from api.dependencies import get_current_user
from models.user import User
from models.user_audit_log import UserAuditLog
from models.subscription import Subscription, SubscriptionPlan
from services.subscription_service import SubscriptionService

router = APIRouter()


# Request/Response Models
class SubscriptionResponse(BaseModel):
    """Subscription response model."""
    id: str
    plan: str
    status: str
    billing_period: str | None
    current_period_start: str | None
    current_period_end: str | None
    cancel_at_period_end: bool
    last_billing_date: str | None
    next_billing_date: str | None
    last_billing_status: str | None
    trial_end_date: str | None
    created_at: str
    updated_at: str
    
    # Removed fields:
    # - user_id: Not needed (user already authenticated via JWT)
    # - paddle_subscription_id: Internal Paddle ID, not used by frontend
    # - price_id: Internal price reference, not needed


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
    
    # Use Pydantic model to ensure only expected fields are returned
    return SubscriptionResponse(
        id=subscription.id,
        plan=subscription.plan.value,
        status=subscription.status.value,
        billing_period=subscription.billing_period.value if subscription.billing_period else None,
        current_period_start=subscription.current_period_start.isoformat() if subscription.current_period_start else None,
        current_period_end=subscription.current_period_end.isoformat() if subscription.current_period_end else None,
        cancel_at_period_end=subscription.cancel_at_period_end,
        last_billing_date=subscription.last_billing_date.isoformat() if subscription.last_billing_date else None,
        next_billing_date=subscription.next_billing_date.isoformat() if subscription.next_billing_date else None,
        last_billing_status=subscription.last_billing_status,
        trial_end_date=subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
        created_at=subscription.created_at.isoformat() if subscription.created_at else "",
        updated_at=subscription.updated_at.isoformat() if subscription.updated_at else "",
    )


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
    
    # Use Pydantic model to ensure only expected fields are returned
    return [
        SubscriptionResponse(
            id=sub.id,
            plan=sub.plan.value,
            status=sub.status.value,
            current_period_start=sub.current_period_start.isoformat() if sub.current_period_start else None,
            current_period_end=sub.current_period_end.isoformat() if sub.current_period_end else None,
            cancel_at_period_end=sub.cancel_at_period_end,
            created_at=sub.created_at.isoformat() if sub.created_at else "",
            updated_at=sub.updated_at.isoformat() if sub.updated_at else "",
        )
        for sub in subscriptions
    ]


@router.post("/create", response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    request: Request,
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
        
        # Create audit log entry for subscription creation
        try:
            ip_address = get_remote_address(request)
            user_agent = request.headers.get("user-agent", "")
            
            audit_log = UserAuditLog(
                user_id=current_user.id,
                action="create_subscription",
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"Created subscription: plan={subscription_data.plan}, billing_period={subscription_data.billing_period}, subscription_id={subscription.id}"
            )
            db.add(audit_log)
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to create audit log for subscription creation: {str(e)}")
        
        # Use Pydantic model to ensure only expected fields are returned
        return SubscriptionResponse(
            id=subscription.id,
            plan=subscription.plan.value,
            status=subscription.status.value,
            current_period_start=subscription.current_period_start.isoformat() if subscription.current_period_start else None,
            current_period_end=subscription.current_period_end.isoformat() if subscription.current_period_end else None,
            cancel_at_period_end=subscription.cancel_at_period_end,
            created_at=subscription.created_at.isoformat() if subscription.created_at else "",
            updated_at=subscription.updated_at.isoformat() if subscription.updated_at else "",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/cancel")
async def cancel_subscription(
    request: Request,
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
    
    # Get IP address from request for audit logging
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
    old_status = subscription.status.value
    old_plan = subscription.plan.value
    
    cancelled = SubscriptionService.cancel_subscription(
        subscription_id=subscription.id,
        user_id=current_user.id,
        cancel_at_period_end=cancel_at_period_end,
        db=db
    )
    
    # Create audit log entry for subscription cancellation
    audit_log = UserAuditLog(
        user_id=current_user.id,
        action="cancel_subscription",
        ip_address=ip_address,
        user_agent=user_agent,
        details=f"Subscription cancelled. Plan: {old_plan}, Status: {old_status} -> {cancelled.status.value}, Cancel at period end: {cancel_at_period_end}"
    )
    db.add(audit_log)
    db.commit()
    
    # Use Pydantic model to ensure only expected fields are returned
    subscription_response = SubscriptionResponse(
        id=cancelled.id,
        plan=cancelled.plan.value,
        status=cancelled.status.value,
        current_period_start=cancelled.current_period_start.isoformat() if cancelled.current_period_start else None,
        current_period_end=cancelled.current_period_end.isoformat() if cancelled.current_period_end else None,
        cancel_at_period_end=cancelled.cancel_at_period_end,
        created_at=cancelled.created_at.isoformat() if cancelled.created_at else "",
        updated_at=cancelled.updated_at.isoformat() if cancelled.updated_at else "",
    )
    
    return {
        "message": "Subscription cancelled successfully",
        "subscription": subscription_response
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


class SubscriptionUpdateRequest(BaseModel):
    """Subscription update request model."""
    plan: str  # starter, professional, power
    billing_period: str = "monthly"  # monthly or yearly
    proration_billing_mode: str = "prorated_immediately"  # Paddle proration mode
    
    class Config:
        json_schema_extra = {
            "example": {
                "plan": "professional",
                "billing_period": "monthly",
                "proration_billing_mode": "prorated_immediately"
            }
        }


@router.put("/update", response_model=SubscriptionResponse)
async def update_subscription(
    request: Request,
    update_data: SubscriptionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update subscription plan (upgrade/downgrade) with proration.
    
    Uses Paddle's subscription update API to change the subscription items
    with automatic proration calculation (similar to Stripe).
    
    **Proration Modes:**
    - `prorated_immediately`: Calculate and bill prorated amount now (Stripe-like, recommended)
    - `prorated_next_billing_period`: Calculate now, bill on next renewal
    - `full_immediately`: Charge full amount now (no proration)
    - `full_next_billing_period`: Charge full amount on next renewal
    - `do_not_bill`: Change without billing
    
    **Authentication Required**: Yes (JWT token)
    
    **Request Body**:
    - plan: New subscription plan (starter, professional, power)
    - billing_period: New billing period (monthly, yearly)
    - proration_billing_mode: How to handle proration (default: prorated_immediately)
    
    **Response 200**:
    - Updated subscription information
    
    **Response 400**: Invalid plan or subscription update failed
    **Response 404**: No active subscription found
    **Response 401**: Not authenticated
    """
    subscription = SubscriptionService.get_active_subscription(current_user.id, db)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription found"
        )
    
    # Validate proration mode
    valid_proration_modes = [
        "prorated_immediately",
        "prorated_next_billing_period",
        "full_immediately",
        "full_next_billing_period",
        "do_not_bill"
    ]
    if update_data.proration_billing_mode not in valid_proration_modes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid proration_billing_mode. Must be one of: {', '.join(valid_proration_modes)}"
        )
    
    # Check if plan is actually changing
    if subscription.plan.value == update_data.plan and \
       subscription.billing_period.value == update_data.billing_period.lower():
        # No change needed
        return SubscriptionResponse(
            id=subscription.id,
            plan=subscription.plan.value,
            status=subscription.status.value,
            billing_period=subscription.billing_period.value if subscription.billing_period else None,
            current_period_start=subscription.current_period_start.isoformat() if subscription.current_period_start else None,
            current_period_end=subscription.current_period_end.isoformat() if subscription.current_period_end else None,
            cancel_at_period_end=subscription.cancel_at_period_end,
            last_billing_date=subscription.last_billing_date.isoformat() if subscription.last_billing_date else None,
            next_billing_date=subscription.next_billing_date.isoformat() if subscription.next_billing_date else None,
            last_billing_status=subscription.last_billing_status,
            trial_end_date=subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
            created_at=subscription.created_at.isoformat() if subscription.created_at else "",
            updated_at=subscription.updated_at.isoformat() if subscription.updated_at else "",
        )
    
    # Get IP address for audit logging
    ip_address = get_remote_address(request)
    user_agent = request.headers.get("user-agent", "")
    
    old_plan = subscription.plan.value
    old_billing_period = subscription.billing_period.value if subscription.billing_period else None
    
    # Update subscription via Paddle API (with proration)
    from services.payment_service import PaymentService
    result = await PaymentService.update_subscription_plan(
        subscription=subscription,
        new_plan=update_data.plan,
        new_billing_period=update_data.billing_period,
        proration_billing_mode=update_data.proration_billing_mode,
        db=db
    )
    
    # Refresh subscription from database
    db.refresh(subscription)
    
    # Create audit log entry
    audit_log = UserAuditLog(
        user_id=current_user.id,
        action="update_subscription",
        ip_address=ip_address,
        user_agent=user_agent,
        details=f"Subscription updated: {old_plan} ({old_billing_period}) -> {update_data.plan} ({update_data.billing_period}), proration_mode: {update_data.proration_billing_mode}"
    )
    db.add(audit_log)
    db.commit()
    
    # Return updated subscription
    return SubscriptionResponse(
        id=subscription.id,
        plan=subscription.plan.value,
        status=subscription.status.value,
        billing_period=subscription.billing_period.value if subscription.billing_period else None,
        current_period_start=subscription.current_period_start.isoformat() if subscription.current_period_start else None,
        current_period_end=subscription.current_period_end.isoformat() if subscription.current_period_end else None,
        cancel_at_period_end=subscription.cancel_at_period_end,
        last_billing_date=subscription.last_billing_date.isoformat() if subscription.last_billing_date else None,
        next_billing_date=subscription.next_billing_date.isoformat() if subscription.next_billing_date else None,
        last_billing_status=subscription.last_billing_status,
        trial_end_date=subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
        created_at=subscription.created_at.isoformat() if subscription.created_at else "",
        updated_at=subscription.updated_at.isoformat() if subscription.updated_at else "",
    )
