"""
Usage Routes

Handles usage tracking and limits.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_db
from api.dependencies import get_current_user
from models.user import User
from services.subscription_service import SubscriptionService
from services.usage_service import UsageService

router = APIRouter()


@router.get("/")
async def get_usage(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get current usage metrics for user.
    
    Returns usage for all metric types (keyword_searches, opportunities_per_month, api_calls_per_month)
    with current counts, limits, and remaining capacity.
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    ```json
    {
      "keyword_searches": {
        "current": 2,
        "limit": 5,
        "allowed": true,
        "remaining": 3
      },
      "opportunities_per_month": {
        "current": 45,
        "limit": 200,
        "allowed": true,
        "remaining": 155
      },
      "api_calls_per_month": {
        "current": 0,
        "limit": 0,
        "allowed": true,
        "remaining": 0
      }
    }
    ```
    
    **Response 404**: No active subscription found
    **Response 401**: Not authenticated
    """
    subscription = SubscriptionService.get_active_subscription(current_user.id, db)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription found"
        )
    
    usage_data = UsageService.get_all_usage(
        user_id=current_user.id,
        subscription_id=subscription.id,
        db=db
    )
    
    return usage_data


@router.get("/limits")
async def get_usage_limits(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get usage limits for current subscription.
    
    Returns the plan limits without current usage counts.
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    - Plan limits
    
    **Response 404**: No active subscription found
    """
    subscription = SubscriptionService.get_active_subscription(current_user.id, db)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription found"
        )
    
    limits = SubscriptionService.get_plan_limits(subscription.plan.value)
    
    return {
        "plan": subscription.plan.value,
        "limits": limits
    }


@router.get("/check/{metric_type}")
async def check_usage_limit(
    metric_type: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Check if user can perform an action (hasn't reached limit).
    
    **Authentication Required**: Yes (JWT token)
    
    **Path Parameters**:
    - metric_type: Type of metric (keyword_searches, opportunities_per_month, api_calls_per_month)
    
    **Response 200**:
    ```json
    {
      "allowed": true,
      "current": 2,
      "limit": 5,
      "remaining": 3
    }
    ```
    
    **Response 404**: No active subscription found
    """
    subscription = SubscriptionService.get_active_subscription(current_user.id, db)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription found"
        )
    
    allowed, current, limit = SubscriptionService.check_usage_limit(
        user_id=current_user.id,
        metric_type=metric_type,
        db=db
    )
    
    return {
        "allowed": allowed,
        "current": current,
        "limit": limit,
        "remaining": max(0, limit - current)
    }
