"""
Opportunity Routes

Handles opportunity management (renamed from "leads" for freelancer focus).
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime
import csv
from io import StringIO
import asyncio

from core.database import get_db
from core.logger import get_logger
from api.dependencies import get_current_user, require_active_subscription
from api.middleware.rate_limit import limiter
from models.user import User
from models.subscription import Subscription
from models.opportunity import Opportunity, OpportunityStatus
from models.keyword_search import KeywordSearch
from services.opportunity_service import OpportunityService
from services.job_service import JobService, JobStatus
from services.subscription_service import SubscriptionService

logger = get_logger(__name__)

router = APIRouter()


# Request/Response Models
class OpportunityUpdate(BaseModel):
    """Opportunity update request model."""
    status: Optional[str] = None  # new, viewed, contacted, applied, rejected, won, lost
    notes: Optional[str] = None


class OpportunityResponse(BaseModel):
    """Opportunity response model."""
    id: str
    keyword_search_id: str
    source_post_id: str
    source: str
    source_type: str
    title: str | None
    content: str
    author: str
    url: str
    matched_keywords: List[str]
    detected_pattern: str | None
    opportunity_type: str | None
    opportunity_subtype: str | None
    relevance_score: float | None
    urgency_score: float | None
    total_score: float | None
    extracted_info: dict | None
    status: str
    notes: str | None
    created_at: str
    updated_at: str
    
    # Removed: user_id - Not needed (user already authenticated via JWT, never used by frontend)


class PaginatedOpportunitiesResponse(BaseModel):
    """Paginated opportunities response."""
    items: List[OpportunityResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


@router.get("/", response_model=PaginatedOpportunitiesResponse)
async def list_opportunities(
    keyword_search_id: Optional[str] = Query(None, description="Filter by keyword search"),
    status: Optional[str] = Query(None, description="Filter by status"),
    source: Optional[str] = Query(None, description="Filter by source platform"),
    limit: int = Query(50, ge=1, le=100, description="Number of results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    current_user: User = Depends(get_current_user),
    subscription: Subscription = Depends(require_active_subscription),
    db: Session = Depends(get_db)
):
    """
    List user's opportunities.
    
    Returns opportunities for the authenticated user with filtering and pagination.
    
    **Authentication Required**: Yes (JWT token)
    **Subscription Required**: Yes (active subscription)
    
    **Query Parameters**:
    - keyword_search_id: Optional filter by keyword search
    - status: Optional filter by status (new, viewed, contacted, etc.)
    - source: Optional filter by source platform (reddit, craigslist, etc.)
    - limit: Number of results (1-100, default: 50)
    - offset: Pagination offset (default: 0)
    
    **Response 200**:
    - List of opportunities
    
    **Response 401**: Not authenticated
    **Response 402**: No active subscription
    """
    # Base query - user-scoped
    query = db.query(Opportunity).filter(Opportunity.user_id == current_user.id)
    
    # Apply filters
    if keyword_search_id:
        # Verify keyword search belongs to user
        keyword_search = db.query(KeywordSearch).filter(
            KeywordSearch.id == keyword_search_id,
            KeywordSearch.user_id == current_user.id
        ).first()
        if not keyword_search:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Keyword search not found"
            )
        query = query.filter(Opportunity.keyword_search_id == keyword_search_id)
    
    if status:
        try:
            status_enum = OpportunityStatus(status)
            query = query.filter(Opportunity.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status}"
            )
    
    if source:
        query = query.filter(Opportunity.source == source)
    
    # Get total count before pagination (for frontend pagination)
    total_count = query.count()
    
    # Order by creation date (newest first)
    query = query.order_by(Opportunity.created_at.desc())
    
    # Pagination
    opportunities = query.offset(offset).limit(limit).all()
    
    # Convert to response models (exclude user_id)
    items = [
        OpportunityResponse(
            id=opp.id,
            keyword_search_id=opp.keyword_search_id,
            source_post_id=opp.source_post_id,
            source=opp.source,
            source_type=opp.source_type,
            title=opp.title,
            content=opp.content,
            author=opp.author,
            url=opp.url,
            matched_keywords=opp.matched_keywords,
            detected_pattern=opp.detected_pattern,
            opportunity_type=opp.opportunity_type,
            opportunity_subtype=opp.opportunity_subtype,
            relevance_score=opp.relevance_score,
            urgency_score=opp.urgency_score,
            total_score=opp.total_score,
            extracted_info=opp.extracted_info,
            status=opp.status.value,
            notes=opp.notes,
            created_at=opp.created_at.isoformat() if opp.created_at else "",
            updated_at=opp.updated_at.isoformat() if opp.updated_at else "",
        )
        for opp in opportunities
    ]
    
    # Return with pagination metadata
    return {
        "items": items,
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(opportunities)) < total_count
    }


@router.get("/{opportunity_id}", response_model=OpportunityResponse)
async def get_opportunity(
    opportunity_id: str,
    current_user: User = Depends(get_current_user),
    subscription: Subscription = Depends(require_active_subscription),
    db: Session = Depends(get_db)
):
    """
    Get opportunity details.
    
    **Authentication Required**: Yes (JWT token)
    **Subscription Required**: Yes (active subscription)
    
    **Path Parameters**:
    - opportunity_id: Opportunity UUID
    
    **Response 200**:
    - Opportunity details
    
    **Response 404**: Opportunity not found or doesn't belong to user
    **Response 401**: Not authenticated
    **Response 402**: No active subscription
    """
    opportunity = db.query(Opportunity).filter(
        Opportunity.id == opportunity_id,
        Opportunity.user_id == current_user.id
    ).first()
    
    if not opportunity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found"
        )
    
    # Use Pydantic model to ensure only expected fields are returned (exclude user_id)
    return OpportunityResponse(
        id=opportunity.id,
        keyword_search_id=opportunity.keyword_search_id,
        source_post_id=opportunity.source_post_id,
        source=opportunity.source,
        source_type=opportunity.source_type,
        title=opportunity.title,
        content=opportunity.content,
        author=opportunity.author,
        url=opportunity.url,
        matched_keywords=opportunity.matched_keywords,
        detected_pattern=opportunity.detected_pattern,
        opportunity_type=opportunity.opportunity_type,
        opportunity_subtype=opportunity.opportunity_subtype,
        relevance_score=opportunity.relevance_score,
        urgency_score=opportunity.urgency_score,
        total_score=opportunity.total_score,
        extracted_info=opportunity.extracted_info,
        status=opportunity.status.value,
        notes=opportunity.notes,
        created_at=opportunity.created_at.isoformat() if opportunity.created_at else "",
        updated_at=opportunity.updated_at.isoformat() if opportunity.updated_at else "",
    )


@router.patch("/{opportunity_id}", response_model=OpportunityResponse)
async def update_opportunity(
    opportunity_id: str,
    opportunity_data: OpportunityUpdate,
    current_user: User = Depends(get_current_user),
    subscription: Subscription = Depends(require_active_subscription),
    db: Session = Depends(get_db)
):
    """
    Update opportunity (status, notes).
    
    **Authentication Required**: Yes (JWT token)
    **Subscription Required**: Yes (active subscription)
    
    **Path Parameters**:
    - opportunity_id: Opportunity UUID
    
    **Request Body**:
    - status: Optional new status
    - notes: Optional user notes
    
    **Response 200**:
    - Updated opportunity
    
    **Response 404**: Opportunity not found
    **Response 400**: Invalid status
    **Response 401**: Not authenticated
    """
    opportunity = db.query(Opportunity).filter(
        Opportunity.id == opportunity_id,
        Opportunity.user_id == current_user.id
    ).first()
    
    if not opportunity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found"
        )
    
    # Update status if provided
    if opportunity_data.status is not None:
        try:
            opportunity.status = OpportunityStatus(opportunity_data.status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {opportunity_data.status}"
            )
    
    # Update notes if provided
    if opportunity_data.notes is not None:
        opportunity.notes = opportunity_data.notes
    
    db.commit()
    db.refresh(opportunity)
    
    # Use Pydantic model to ensure only expected fields are returned (exclude user_id)
    return OpportunityResponse(
        id=opportunity.id,
        keyword_search_id=opportunity.keyword_search_id,
        source_post_id=opportunity.source_post_id,
        source=opportunity.source,
        source_type=opportunity.source_type,
        title=opportunity.title,
        content=opportunity.content,
        author=opportunity.author,
        url=opportunity.url,
        matched_keywords=opportunity.matched_keywords,
        detected_pattern=opportunity.detected_pattern,
        opportunity_type=opportunity.opportunity_type,
        opportunity_subtype=opportunity.opportunity_subtype,
        relevance_score=opportunity.relevance_score,
        urgency_score=opportunity.urgency_score,
        total_score=opportunity.total_score,
        extracted_info=opportunity.extracted_info,
        status=opportunity.status.value,
        notes=opportunity.notes,
        created_at=opportunity.created_at.isoformat() if opportunity.created_at else "",
        updated_at=opportunity.updated_at.isoformat() if opportunity.updated_at else "",
    )


@router.delete("/{opportunity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_opportunity(
    opportunity_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete opportunity.
    
    **Authentication Required**: Yes (JWT token)
    
    **Path Parameters**:
    - opportunity_id: Opportunity UUID
    
    **Response 204**: Successfully deleted
    
    **Response 404**: Opportunity not found
    **Response 401**: Not authenticated
    """
    opportunity = db.query(Opportunity).filter(
        Opportunity.id == opportunity_id,
        Opportunity.user_id == current_user.id
    ).first()
    
    if not opportunity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found"
        )
    
    db.delete(opportunity)
    db.commit()
    
    return None


async def process_opportunity_generation(
    job_id: str,
    keyword_search_id: str,
    user_id: str,
    subscription_id: str,
    limit: int,
    force_refresh: bool = False
):
    """
    Background task to generate opportunities.
    
    Note: Creates its own database session since background tasks run after response is sent.
    
    Args:
        job_id: Job UUID
        keyword_search_id: Keyword search UUID
        user_id: User UUID
        subscription_id: Subscription UUID
        limit: Maximum opportunities to generate
    """
    # Create new database session for background task
    from core.database import SessionLocal
    db = SessionLocal()
    
    try:
        # Update status to processing
        JobService.update_job_status(
            job_id=job_id,
            status=JobStatus.PROCESSING,
            progress=10,
            message="Creating search in Rixly..."
        )
        
        # Generate opportunities
        result = await OpportunityService.generate_opportunities(
            keyword_search_id=keyword_search_id,
            user_id=user_id,
            subscription_id=subscription_id,
            db=db,
            limit=limit,
            force_refresh=force_refresh
        )
        
        # Update status to completed
        # Include cooldown message in job message if present
        job_message = result.get('message', f"Successfully generated {result.get('opportunities_created', 0)} opportunities")
        
        JobService.update_job_status(
            job_id=job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message=job_message,
            result=result
        )
        
        logger.info(f"Job {job_id} completed successfully")
        
    except HTTPException as e:
        # Handle HTTP exceptions (like 409 cooldown) specially
        error_message = e.detail if hasattr(e, 'detail') else str(e)
        
        if e.status_code == 409:  # Cooldown conflict
            # This shouldn't happen here since we handle 409 in the service
            # But if it does, update job with cooldown message
            JobService.update_job_status(
                job_id=job_id,
                status=JobStatus.PROCESSING,
                progress=50,
                message=f"Cooldown active: {error_message}. Using existing leads...",
                error=None
            )
            
            # The service should have already handled this and continued with existing leads
            # This is a fallback in case the exception propagates
            logger.warning(f"Cooldown exception reached background task - this should be handled in service")
        else:
            # Other HTTP exceptions - mark as failed
            logger.error(f"Job {job_id} failed with HTTP {e.status_code}: {error_message}", exc_info=True)
            JobService.update_job_status(
                job_id=job_id,
                status=JobStatus.FAILED,
                progress=0,
                message=f"Failed to generate opportunities: {error_message}",
                error=error_message
            )
    except Exception as e:
        logger.error(f"Job {job_id} failed: {str(e)}", exc_info=True)
        JobService.update_job_status(
            job_id=job_id,
            status=JobStatus.FAILED,
            progress=0,
            message="Failed to generate opportunities",
            error=str(e)
        )
    finally:
        db.close()


@router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("10/minute")
async def generate_opportunities(
    request: Request,
    keyword_search_id: str = Query(..., description="Keyword search UUID to generate opportunities for"),
    limit: int = Query(100, ge=1, le=500, description="Maximum opportunities to generate"),
    force_refresh: bool = Query(False, description="Force new scrape even if leads exist (respects cooldown)"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    subscription: Subscription = Depends(require_active_subscription),
    db: Session = Depends(get_db)
):
    """
    Generate opportunities from Reddit by calling Rixly API (async background job).
    
    This endpoint:
    1. Creates a background job and returns immediately
    2. Processes scraping and opportunity generation in background
    3. User can poll /api/v1/opportunities/generate/{job_id}/status for progress
    
    **SECURITY**: Rate limited to 10 requests per minute per IP to prevent abuse.
    
    **Authentication Required**: Yes (JWT token)
    **Subscription Required**: Yes (active subscription)
    
    **Query Parameters**:
    - keyword_search_id: Keyword search UUID to generate opportunities for (required)
    - limit: Maximum number of opportunities to generate (1-500, default: 100)
    - force_refresh: Force new scrape even if leads exist (default: false, respects cooldown)
    
    **Response 202 Accepted**:
    ```json
    {
      "job_id": "uuid-here",
      "status": "pending",
      "message": "Job queued. Poll /api/v1/opportunities/generate/{job_id}/status for progress."
    }
    ```
    
    **Response 402**: Monthly opportunity limit reached
    **Response 404**: Keyword search not found
    **Response 400**: Keyword search is disabled
    **Response 429**: Rate limit exceeded
    """
    
    # Validate keyword search
    keyword_search = db.query(KeywordSearch).filter(
        KeywordSearch.id == keyword_search_id,
        KeywordSearch.user_id == current_user.id
    ).first()
    
    if not keyword_search:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword search not found"
        )
    
    if not keyword_search.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Keyword search is disabled. Enable it first to generate opportunities."
        )
    
    # Check opportunity limit
    allowed, current, limit_count = SubscriptionService.check_usage_limit(
        user_id=current_user.id,
        metric_type="opportunities_per_month",
        db=db
    )
    
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Monthly opportunity limit reached ({current}/{limit_count}). "
                   f"Upgrade your plan or wait for the next billing period."
        )
    
    # Create job
    job_id = JobService.create_job(
        user_id=current_user.id,
        keyword_search_id=keyword_search_id,
        limit=limit
    )
    
    # Add background task (don't pass db session - it will be closed)
    background_tasks.add_task(
        process_opportunity_generation,
        job_id=job_id,
        keyword_search_id=keyword_search_id,
        user_id=current_user.id,
        subscription_id=subscription.id,
        limit=limit,
        force_refresh=force_refresh
    )
    
    return {
        "job_id": job_id,
        "status": "pending",
        "message": f"Job queued. Poll /api/v1/opportunities/generate/{job_id}/status for progress."
    }


@router.get("/generate/active/{keyword_search_id}")
async def get_active_job_for_search(
    keyword_search_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get active (pending/processing) job for a keyword search.
    
    **Authentication Required**: Yes (JWT token)
    
    **Path Parameters**:
    - keyword_search_id: Keyword search UUID
    
    **Response 200**:
    ```json
    {
      "job_id": "uuid-here",
      "status": "processing",
      "progress": 50,
      "message": "Fetching leads from Rixly...",
      "created_at": "2025-11-09T10:00:00Z",
      "updated_at": "2025-11-09T10:00:30Z"
    }
    ```
    
    **Response 404**: No active job found
    """
    # Get all user jobs
    user_jobs = JobService.get_user_jobs(current_user.id)
    
    # Find active job for this keyword search
    active_job = None
    for job in user_jobs:
        if (job["keyword_search_id"] == keyword_search_id and 
            job["status"] in [JobStatus.PENDING, JobStatus.PROCESSING]):
            active_job = job
            break
    
    if not active_job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active job found for this keyword search"
        )
    
    return {
        "job_id": active_job["id"],
        "status": active_job["status"],
        "progress": active_job["progress"],
        "message": active_job["message"],
        "created_at": active_job["created_at"].isoformat(),
        "updated_at": active_job["updated_at"].isoformat()
    }


@router.get("/generate/{job_id}/status")
async def get_generation_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get status of opportunity generation job.
    
    **Authentication Required**: Yes (JWT token)
    
    **Path Parameters**:
    - job_id: Job UUID
    
    **Response 200**:
    ```json
    {
      "job_id": "uuid-here",
      "status": "processing",
      "progress": 50,
      "message": "Fetching leads from Rixly...",
      "result": null,
      "error": null,
      "created_at": "2025-11-09T10:00:00Z",
      "updated_at": "2025-11-09T10:00:30Z"
    }
    ```
    
    **Response 404**: Job not found or doesn't belong to user
    """
    job = JobService.get_job(job_id)
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found. Job ID: {job_id}. The job may have been completed and cleaned up, or the ID is incorrect."
        )
    
    # Verify job belongs to user
    if job["user_id"] != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found. This job does not belong to your account."
        )
    
    return {
        "job_id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "result": job["result"],
        "error": job["error"],
        "created_at": job["created_at"].isoformat(),
        "updated_at": job["updated_at"].isoformat()
    }


@router.get("/export/csv")
async def export_opportunities_csv(
    keyword_search_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    subscription: Subscription = Depends(require_active_subscription),
    db: Session = Depends(get_db)
):
    """
    Export opportunities as CSV.
    
    Exports user's opportunities as a CSV file download.
    
    **Authentication Required**: Yes (JWT token)
    **Subscription Required**: Yes (active subscription)
    
    **Query Parameters**:
    - keyword_search_id: Filter by keyword search (optional)
    - status: Filter by status (optional)
    
    **Response 200**: CSV file download
    
    **Response 401**: Not authenticated
    """
    # Build query (same as list_opportunities)
    query = db.query(Opportunity).filter(Opportunity.user_id == current_user.id)
    
    if keyword_search_id:
        query = query.filter(Opportunity.keyword_search_id == keyword_search_id)
    
    if status:
        try:
            status_enum = OpportunityStatus(status)
            query = query.filter(Opportunity.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status}"
            )
    
    opportunities = query.order_by(Opportunity.created_at.desc()).all()
    
    # Create CSV content
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        "ID", "Title", "Source", "URL", "Author", "Status",
        "Relevance Score", "Urgency Score", "Total Score",
        "Opportunity Type", "Matched Keywords", "Created At", "Notes"
    ])
    
    # Write data rows
    for opp in opportunities:
        writer.writerow([
            opp.id,
            opp.title or "",
            opp.source,
            opp.url,
            opp.author,
            opp.status.value if opp.status else "",
            opp.relevance_score,
            opp.urgency_score,
            opp.total_score,
            opp.opportunity_type or "",
            ", ".join(opp.matched_keywords) if isinstance(opp.matched_keywords, list) else str(opp.matched_keywords),
            opp.created_at.isoformat() if opp.created_at else "",
            opp.notes or ""
        ])
    
    output.seek(0)
    
    # Return CSV file
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=opportunities_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        }
    )


@router.get("/export/json")
async def export_opportunities_json(
    keyword_search_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    subscription: Subscription = Depends(require_active_subscription),
    db: Session = Depends(get_db)
):
    """
    Export opportunities as JSON.
    
    **Authentication Required**: Yes (JWT token)
    **Subscription Required**: Yes (active subscription)
    
    **Response 200**: JSON array of opportunities
    
    **Response 401**: Not authenticated
    """
    # Build query (same as list_opportunities)
    query = db.query(Opportunity).filter(Opportunity.user_id == current_user.id)
    
    if keyword_search_id:
        query = query.filter(Opportunity.keyword_search_id == keyword_search_id)
    
    if status:
        try:
            status_enum = OpportunityStatus(status)
            query = query.filter(Opportunity.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status}"
            )
    
    opportunities = query.order_by(Opportunity.created_at.desc()).all()
    
    # Convert to response models (exclude user_id)
    opportunity_responses = [
        OpportunityResponse(
            id=opp.id,
            keyword_search_id=opp.keyword_search_id,
            source_post_id=opp.source_post_id,
            source=opp.source,
            source_type=opp.source_type,
            title=opp.title,
            content=opp.content,
            author=opp.author,
            url=opp.url,
            matched_keywords=opp.matched_keywords,
            detected_pattern=opp.detected_pattern,
            opportunity_type=opp.opportunity_type,
            opportunity_subtype=opp.opportunity_subtype,
            relevance_score=opp.relevance_score,
            urgency_score=opp.urgency_score,
            total_score=opp.total_score,
            extracted_info=opp.extracted_info,
            status=opp.status.value,
            notes=opp.notes,
            created_at=opp.created_at.isoformat() if opp.created_at else "",
            updated_at=opp.updated_at.isoformat() if opp.updated_at else "",
        )
        for opp in opportunities
    ]
    
    return {
        "total": len(opportunities),
        "opportunities": opportunity_responses
    }
