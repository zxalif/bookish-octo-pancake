"""
E2E Test Routes

Endpoints for running and viewing end-to-end test results.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime

from core.database import get_db
from api.dependencies import get_admin_user
from api.middleware.rate_limit import limiter
from models.user import User
from models.e2e_test_result import E2ETestResult
from services.e2e_test_service import E2ETestService
from core.logger import get_logger
from core.config import get_settings

settings = get_settings()
logger = get_logger(__name__)

router = APIRouter()

# Note: E2E tests are now processed by a separate worker service
# The API only queues jobs in Redis - no semaphore needed here


class E2ETestRunRequest(BaseModel):
    """Request model for running E2E tests."""
    triggered_by: str = "manual"  # manual, scheduled, deployment
    frontend_url: Optional[str] = None
    api_url: Optional[str] = None


class E2ETestResultResponse(BaseModel):
    """Response model for E2E test result."""
    id: str
    test_run_id: str
    status: str
    triggered_by: Optional[str]
    test_user_email: Optional[str]
    test_user_id: Optional[str]
    duration_ms: Optional[float]
    steps: Optional[List[Dict[str, Any]]]
    error_message: Optional[str]
    screenshot_path: Optional[str]
    metadata: Optional[Dict[str, Any]]
    created_at: str
    updated_at: str


def queue_e2e_test_job(
    triggered_by: str,
    frontend_url: Optional[str] = None,
    api_url: Optional[str] = None
) -> str:
    """
    Queue E2E test job in Redis for worker service to process.
    
    This is a synchronous function that queues the job in Redis.
    The actual test execution happens in the isolated E2E worker service.
    
    Returns:
        str: Job ID
    """
    import uuid
    import json
    from core.redis_client import get_redis_client, is_redis_available
    
    # Generate job ID
    job_id = str(uuid.uuid4())
    
    # Check Redis availability
    if not is_redis_available():
        raise Exception("Redis is not available. Cannot queue E2E test job.")
    
    redis_client = get_redis_client()
    if not redis_client:
        raise Exception("Failed to get Redis client. Cannot queue E2E test job.")
    
    # Create job data
    job_data = {
        "job_id": job_id,
        "triggered_by": triggered_by,
        "frontend_url": frontend_url,
        "api_url": api_url,
        "created_at": datetime.utcnow().isoformat()
    }
    
    # Push job to Redis queue
    redis_client.rpush("e2e_test_jobs", json.dumps(job_data))
    
    logger.info(f"Queued E2E test job {job_id} in Redis (triggered_by: {triggered_by})")
    
    return job_id


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("500/hour")  # Limit to 5 runs per hour
async def run_e2e_test(
    request: Request,
    test_request: E2ETestRunRequest = E2ETestRunRequest(),
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Queue end-to-end test job in Redis for isolated worker service.
    
    Tests the complete user journey:
    1. Register user
    2. Verify email
    3. Login
    4. Create keyword search
    5. Generate opportunities
    6. Create support thread
    
    **IMPORTANT**: 
    - This endpoint returns immediately (202 Accepted). The job is queued in Redis.
    - A separate E2E worker service processes jobs from Redis queue.
    - Tests run in complete isolation from the API server (no resource contention).
    - Check /api/v1/e2e-tests/results to see test results.
    
    **Architecture**:
    - API queues job in Redis (fast, non-blocking)
    - E2E worker service polls Redis and executes tests
    - Results stored in database and available via API
    
    **Admin Only**: Requires admin role.
    
    **Response 202 Accepted**: Job queued
    ```json
    {
      "job_id": "uuid-here",
      "message": "E2E test job queued. Worker service will process it shortly.",
      "triggered_by": "manual"
    }
    ```
    
    **Response 403**: Not an admin
    **Response 503**: Redis unavailable (worker service cannot process jobs)
    """
    logger.info(f"Admin {admin_user.email} triggered E2E test run (triggered_by: {test_request.triggered_by})")
    
    try:
        # Queue job in Redis (synchronous, fast operation)
        job_id = queue_e2e_test_job(
            triggered_by=test_request.triggered_by,
            frontend_url=test_request.frontend_url,
            api_url=test_request.api_url
        )
        
        return {
            "job_id": job_id,
            "message": "E2E test job queued. Worker service will process it shortly.",
            "triggered_by": test_request.triggered_by
        }
    except Exception as e:
        logger.error(f"Failed to queue E2E test job: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to queue E2E test job: {str(e)}. Worker service may be unavailable."
        )


@router.get("/results", response_model=List[E2ETestResultResponse])
@limiter.limit("60/minute")
async def get_e2e_test_results(
    request: Request,
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results to return"),
    status_filter: Optional[str] = Query(None, description="Filter by status (passed, failed, error)"),
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get E2E test results.
    
    **Admin Only**: Requires admin role.
    
    **Query Parameters**:
    - limit: Maximum number of results (default: 50, max: 100)
    - status_filter: Filter by status (passed, failed, error)
    
    **Response 200**: List of test results
    **Response 403**: Not an admin
    """
    query = db.query(E2ETestResult)
    
    if status_filter:
        query = query.filter(E2ETestResult.status == status_filter)
    
    results = query.order_by(desc(E2ETestResult.created_at)).limit(limit).all()
    
    return [E2ETestResultResponse(**result.to_dict()) for result in results]


@router.get("/results/{test_run_id}", response_model=E2ETestResultResponse)
@limiter.limit("60/minute")
async def get_e2e_test_result(
    request: Request,
    test_run_id: str,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get specific E2E test result by test_run_id.
    
    **Admin Only**: Requires admin role.
    
    **Path Parameters**:
    - test_run_id: Test run UUID
    
    **Response 200**: Test result details
    **Response 404**: Test result not found
    **Response 403**: Not an admin
    """
    result = db.query(E2ETestResult).filter(
        E2ETestResult.test_run_id == test_run_id
    ).first()
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Test result not found: {test_run_id}"
        )
    
    return E2ETestResultResponse(**result.to_dict())


@router.get("/stats", response_model=Dict[str, Any])
@limiter.limit("60/minute")
async def get_e2e_test_stats(
    request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get E2E test statistics.
    
    **Admin Only**: Requires admin role.
    
    **Response 200**: Test statistics (total runs, pass rate, average duration, etc.)
    **Response 403**: Not an admin
    """
    total_runs = db.query(E2ETestResult).count()
    
    passed = db.query(E2ETestResult).filter(E2ETestResult.status == "passed").count()
    failed = db.query(E2ETestResult).filter(E2ETestResult.status == "failed").count()
    error = db.query(E2ETestResult).filter(E2ETestResult.status == "error").count()
    
    pass_rate = (passed / total_runs * 100) if total_runs > 0 else 0
    
    # Get average duration
    avg_duration = db.query(func.avg(E2ETestResult.duration_ms)).scalar() or 0
    
    # Get latest test result
    latest = db.query(E2ETestResult).order_by(desc(E2ETestResult.created_at)).first()
    
    return {
        "total_runs": total_runs,
        "passed": passed,
        "failed": failed,
        "error": error,
        "pass_rate": round(pass_rate, 2),
        "average_duration_ms": round(avg_duration, 2),
        "latest_test": {
            "test_run_id": latest.test_run_id if latest else None,
            "status": latest.status if latest else None,
            "created_at": latest.created_at.isoformat() if latest else None
        } if latest else None
    }


@router.delete("/clear", status_code=status.HTTP_200_OK)
@limiter.limit("5/hour")
async def clear_e2e_tests(
    request: Request,
    clear_queue: bool = Query(True, description="Also clear pending jobs from Redis queue"),
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Clear all E2E test results from database and optionally clear the job queue.
    
    **WARNING**: This action cannot be undone!
    
    Args:
        clear_queue: If True, also clears all pending jobs from Redis queue
    """
    try:
        # Delete all test results from database
        deleted_count = db.query(E2ETestResult).delete()
        db.commit()
        
        logger.info(f"Admin {admin_user.email} cleared {deleted_count} E2E test results from database")
        
        queue_cleared = False
        queue_size = 0
        
        # Clear Redis queue if requested
        if clear_queue:
            try:
                from core.redis_client import get_redis_client, is_redis_available
                
                if is_redis_available():
                    redis_client = get_redis_client()
                    if redis_client:
                        # Get queue size before clearing
                        queue_size = redis_client.llen("e2e_test_jobs")
                        
                        # Clear the queue
                        redis_client.delete("e2e_test_jobs")
                        queue_cleared = True
                        
                        logger.info(f"Admin {admin_user.email} cleared {queue_size} pending E2E test jobs from Redis queue")
                    else:
                        logger.warning("Redis client not available - queue not cleared")
                else:
                    logger.warning("Redis not available - queue not cleared")
            except Exception as e:
                logger.error(f"Failed to clear Redis queue: {str(e)}", exc_info=True)
        
        return {
            "message": "E2E tests cleared successfully",
            "deleted_results": deleted_count,
            "queue_cleared": queue_cleared,
            "queue_size_before_clear": queue_size if queue_cleared else None
        }
    except Exception as e:
        logger.error(f"Failed to clear E2E tests: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear E2E tests: {str(e)}"
        )

