"""
Cleanup Routes

Handles cleanup operations (admin/maintenance endpoints).
These endpoints are designed to be called by external schedulers (cron), not run inside the API.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import Optional

from core.database import get_db
from core.config import get_settings
from core.logger import get_logger
from services.cleanup_service import CleanupService

logger = get_logger(__name__)

router = APIRouter()
settings = get_settings()


def verify_service_token(x_service_token: Optional[str] = Header(None, alias="X-Service-Token")):
    """
    Verify service token for scheduled job authentication.
    
    This allows external schedulers (cron) to call cleanup endpoints without user JWT.
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


@router.post("/cleanup/old-searches")
async def cleanup_old_searches(
    days_old: int = 30,
    _: bool = Depends(verify_service_token),
    db: Session = Depends(get_db)
):
    """
    Cleanup old soft-deleted keyword searches.
    
    Permanently deletes keyword searches that were soft-deleted more than X days ago.
    This frees up database space while maintaining the abuse prevention mechanism.
    
    **Designed for External Scheduler**: This endpoint should be called by cron/scheduler, not run inside the API.
    
    **Authentication**: Service token via X-Service-Token header (not user JWT)
    
    **Query Parameters**:
    - days_old: Delete searches soft-deleted more than this many days ago (default: 30)
    
    **Headers**:
    - X-Service-Token: Service token (set SERVICE_TOKEN in environment)
    
    **Response 200**:
    ```json
    {
      "deleted_count": 5,
      "message": "Permanently deleted 5 old soft-deleted searches"
    }
    ```
    
    **Example Cron Job**:
    ```bash
    # Run daily at 2 AM
    0 2 * * * curl -X POST http://localhost:7300/api/v1/cleanup/old-searches?days_old=30 \
      -H "X-Service-Token: YOUR_SERVICE_TOKEN"
    ```
    """
    deleted_count = CleanupService.cleanup_old_soft_deleted_searches(db, days_old)
    
    logger.info(f"Cleanup job completed: Permanently deleted {deleted_count} old soft-deleted searches")
    
    return {
        "deleted_count": deleted_count,
        "message": f"Permanently deleted {deleted_count} old soft-deleted searches"
    }


@router.post("/cleanup/monthly-reset")
async def monthly_cleanup_reset(
    _: bool = Depends(verify_service_token),
    db: Session = Depends(get_db)
):
    """
    Cleanup soft-deleted searches from previous month.
    
    **Designed for External Scheduler**: This endpoint MUST be called by cron/scheduler on the 1st of each month.
    The API does NOT run this automatically - it's the scheduler's responsibility.
    
    Permanently deletes soft-deleted searches from previous month, freeing up slots.
    
    **Authentication**: Service token via X-Service-Token header (not user JWT)
    
    **Headers**:
    - X-Service-Token: Service token (set SERVICE_TOKEN in environment)
    
    **Response 200**:
    ```json
    {
      "deleted_count": 3,
      "message": "Freed 3 slots by deleting previous month's soft-deleted searches"
    }
    ```
    
    **Example Cron Job**:
    ```bash
    # Run on 1st of each month at 00:01
    1 0 1 * * curl -X POST http://localhost:7300/api/v1/cleanup/monthly-reset \
      -H "X-Service-Token: YOUR_SERVICE_TOKEN"
    ```
    """
    deleted_count = CleanupService.cleanup_current_month_soft_deleted_searches(db)
    
    logger.info(
        f"Monthly cleanup job completed: Freed {deleted_count} slots by deleting "
        f"previous month's soft-deleted searches"
    )
    
    return {
        "deleted_count": deleted_count,
        "message": f"Freed {deleted_count} slots by deleting previous month's soft-deleted searches"
    }

