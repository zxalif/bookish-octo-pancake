"""
Subscription Management Jobs

Scheduled jobs for subscription lifecycle management.
These endpoints are called by external schedulers (cron).
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional

from core.database import get_db
from core.logger import get_logger
from core.config import get_settings
from services.subscription_management_service import SubscriptionManagementService

settings = get_settings()
logger = get_logger(__name__)


def verify_service_token(x_service_token: Optional[str] = Header(None, alias="X-Service-Token")):
    """
    Verify service token for scheduled job authentication.
    
    This allows external schedulers (cron) to call endpoints without user JWT.
    Set SERVICE_TOKEN in environment variables.
    """
    service_token = getattr(settings, "SERVICE_TOKEN", None)
    
    if not service_token:
        # If no service token configured, allow in development only
        if settings.ENVIRONMENT == "development":
            logger.warning("SERVICE_TOKEN not configured - allowing in development mode only")
            return True
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Service token not configured"
            )
    
    if not x_service_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Service token required. Set X-Service-Token header."
        )
    
    if x_service_token != service_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service token"
        )
    
    return True

router = APIRouter()


@router.post("/sync-subscriptions")
async def sync_subscriptions_job(
    _: bool = Depends(verify_service_token),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Sync subscription status with Paddle.
    
    This job should be run periodically (e.g., hourly) to ensure our database
    is in sync with Paddle's subscription status.
    
    **Authentication**: Service token required
    
    **Cron Schedule**: `0 * * * *` (every hour)
    
    **Response 200**:
    - Sync results with counts
    """
    try:
        result = SubscriptionManagementService.sync_subscriptions_with_paddle(db)
        logger.info(f"Subscription sync job completed: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in sync_subscriptions_job: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error syncing subscriptions: {str(e)}"
        )


@router.post("/process-expired")
async def process_expired_subscriptions_job(
    _: bool = Depends(verify_service_token),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Process expired subscriptions.
    
    Marks subscriptions as expired if their period has ended.
    
    **Authentication**: Service token required
    
    **Cron Schedule**: `0 2 * * *` (daily at 2 AM)
    
    **Response 200**:
    - Processing results
    """
    try:
        result = SubscriptionManagementService.process_expired_subscriptions(db)
        logger.info(f"Expired subscriptions job completed: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in process_expired_subscriptions_job: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing expired subscriptions: {str(e)}"
        )


@router.post("/process-past-due")
async def process_past_due_subscriptions_job(
    _: bool = Depends(verify_service_token),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Process past_due subscriptions.
    
    Handles past_due subscriptions - syncs with Paddle to check if payment
    was retried successfully, or marks as expired if past_due for too long.
    
    **Authentication**: Service token required
    
    **Cron Schedule**: `0 3 * * *` (daily at 3 AM)
    
    **Response 200**:
    - Processing results
    """
    try:
        result = SubscriptionManagementService.process_past_due_subscriptions(db)
        logger.info(f"Past due subscriptions job completed: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in process_past_due_subscriptions_job: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing past_due subscriptions: {str(e)}"
        )


@router.post("/check-upcoming-renewals")
async def check_upcoming_renewals_job(
    days_ahead: int = 3,
    _: bool = Depends(verify_service_token),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Check for upcoming subscription renewals.
    
    Finds subscriptions with renewals in the next N days.
    Can be used to send renewal reminder emails.
    
    **Authentication**: Service token required
    
    **Cron Schedule**: `0 9 * * *` (daily at 9 AM)
    
    **Query Parameters**:
    - days_ahead: Number of days ahead to check (default: 3)
    
    **Response 200**:
    - Check results
    """
    try:
        result = SubscriptionManagementService.check_upcoming_renewals(db, days_ahead=days_ahead)
        logger.info(f"Upcoming renewals check completed: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in check_upcoming_renewals_job: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error checking upcoming renewals: {str(e)}"
        )


@router.post("/refresh-usage-metrics")
async def refresh_usage_metrics_job(
    _: bool = Depends(verify_service_token),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Refresh usage metrics for all active subscriptions.
    
    This job:
    - Creates usage metrics for current period if they don't exist
    - Cleans up expired usage metrics from previous periods
    - Ensures all active subscriptions have current usage metrics
    
    **Authentication**: Service token required
    
    **Cron Schedule**: `0 0 * * *` (daily at midnight)
    
    **Response 200**:
    - Refresh results
    """
    try:
        result = SubscriptionManagementService.refresh_usage_metrics(db)
        logger.info(f"Usage metrics refresh completed: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in refresh_usage_metrics_job: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error refreshing usage metrics: {str(e)}"
        )

