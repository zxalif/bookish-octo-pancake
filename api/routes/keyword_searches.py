"""
Keyword Search Routes

Handles keyword search management (freelancer-focused).
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from uuid import UUID

from core.database import get_db
from core.config import settings
from api.dependencies import get_current_user, require_active_subscription
from models.user import User
from models.subscription import Subscription
from models.keyword_search import KeywordSearch
from services.subscription_service import SubscriptionService
from services.opportunity_service import OpportunityService
from services.usage_service import UsageService
from core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


# Request/Response Models
class KeywordSearchCreate(BaseModel):
    """Keyword search creation request model."""
    name: str
    keywords: List[str]
    patterns: List[str] = ["looking for", "need", "hiring", "want"]
    subreddits: List[str] = ["forhire", "hiring", "freelance"]
    platforms: List[str] = ["reddit"]  # reddit, craigslist, linkedin, twitter
    enabled: bool = True
    scraping_mode: str = "one_time"  # "one_time" or "scheduled"
    scraping_interval: Optional[str] = None  # "30m", "1h", "6h", "24h" (only for scheduled mode)
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "React Developers",
                "keywords": ["react", "reactjs", "frontend developer"],
                "patterns": ["looking for", "need", "hiring"],
                "subreddits": ["forhire", "hiring"],
                "platforms": ["reddit"],
                "enabled": True
            }
        }


class KeywordSearchUpdate(BaseModel):
    """Keyword search update request model."""
    name: Optional[str] = None
    keywords: Optional[List[str]] = None
    patterns: Optional[List[str]] = None
    subreddits: Optional[List[str]] = None
    platforms: Optional[List[str]] = None
    enabled: Optional[bool] = None
    scraping_mode: Optional[str] = None  # "one_time" or "scheduled"
    scraping_interval: Optional[str] = None  # "30m", "1h", "6h", "24h" (only for scheduled mode)


class KeywordSearchResponse(BaseModel):
    """Keyword search response model."""
    id: str
    name: str
    keywords: List[str]
    patterns: List[str]
    subreddits: List[str]
    platforms: List[str]
    enabled: bool
    scraping_mode: str
    scraping_interval: Optional[str] = None
    created_at: str
    updated_at: str
    
    # Removed fields:
    # - user_id: Not needed (user already authenticated via JWT)
    # - last_run_at: Not used by frontend
    # - zola_search_id: Internal Rixly ID, should not be exposed
    # - deleted_at: Internal soft-delete timestamp, should not be exposed


@router.get("/", response_model=List[KeywordSearchResponse])
async def list_keyword_searches(
    enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    include_deleted: bool = Query(False, description="Include soft-deleted searches"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List user's keyword searches.
    
    Returns all keyword searches for the authenticated user.
    By default, excludes soft-deleted searches.
    
    **Authentication Required**: Yes (JWT token)
    
    **Query Parameters**:
    - enabled: Optional filter by enabled status (true/false)
    - include_deleted: Include soft-deleted searches (default: false)
    
    **Response 200**:
    - List of keyword searches
    
    **Response 401**: Not authenticated
    """
    query = db.query(KeywordSearch).filter(KeywordSearch.user_id == current_user.id)
    
    # Exclude soft-deleted by default
    if not include_deleted:
        query = query.filter(KeywordSearch.deleted_at.is_(None))  # type: ignore
    
    if enabled is not None:
        query = query.filter(KeywordSearch.enabled == enabled)
    
    searches = query.order_by(KeywordSearch.created_at.desc()).all()
    
    # Use Pydantic model to ensure only expected fields are returned
    return [
        KeywordSearchResponse(
            id=search.id,
            name=search.name,
            keywords=search.keywords,
            patterns=search.patterns or [],
            subreddits=search.subreddits or [],
            platforms=search.platforms or ["reddit"],
            enabled=search.enabled,
            scraping_mode=getattr(search, 'scraping_mode', 'one_time'),  # Backward compatibility
            scraping_interval=getattr(search, 'scraping_interval', None),  # Backward compatibility
            created_at=search.created_at.isoformat() if search.created_at else "",
            updated_at=search.updated_at.isoformat() if search.updated_at else "",
        )
        for search in searches
    ]


@router.post("/", response_model=KeywordSearchResponse, status_code=status.HTTP_201_CREATED)
async def create_keyword_search(
    search_data: KeywordSearchCreate,
    current_user: User = Depends(get_current_user),
    subscription: Subscription = Depends(require_active_subscription),
    db: Session = Depends(get_db)
):
    """
    Create new keyword search.
    
    IMPORTANT: This checks CONCURRENT keyword search limit.
    - Users can enable/disable/delete searches anytime
    - Limit is about how many searches are ACTIVE at once
    - If limit reached, user must disable/delete an existing search
    
    **Authentication Required**: Yes (JWT token)
    **Subscription Required**: Yes (active subscription)
    
    **Request Body**:
    - name: Search name
    - keywords: List of keywords to search
    - patterns: List of patterns to match (optional)
    - subreddits: List of subreddits (optional)
    - platforms: List of platforms (optional, default: ["reddit"])
    - enabled: Whether to enable immediately (default: true)
    
    **Response 201**:
    - Created keyword search
    
    **Response 402**: Concurrent keyword search limit reached
    **Response 401**: Not authenticated
    """
    # Validate keywords and subreddits limits
    if len(search_data.keywords) > settings.MAX_KEYWORDS_PER_SEARCH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {settings.MAX_KEYWORDS_PER_SEARCH} keywords allowed per search. "
                   f"Received {len(search_data.keywords)} keywords."
        )
    
    if search_data.subreddits and len(search_data.subreddits) > settings.MAX_SUBREDDITS_PER_SEARCH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {settings.MAX_SUBREDDITS_PER_SEARCH} subreddits allowed per search. "
                   f"Received {len(search_data.subreddits)} subreddits."
        )
    
    # Validate scraping_mode
    if search_data.scraping_mode not in ["one_time", "scheduled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scraping_mode must be 'one_time' or 'scheduled'"
        )
    
    # Validate scraping_interval (required for scheduled mode)
    if search_data.scraping_mode == "scheduled":
        if not search_data.scraping_interval:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scraping_interval is required when scraping_mode is 'scheduled'"
            )
        if search_data.scraping_interval not in ["30m", "1h", "6h", "24h"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scraping_interval must be one of: '30m', '1h', '6h', '24h'"
            )
    elif search_data.scraping_interval:
        # Clear interval if mode is one_time
        search_data.scraping_interval = None
    
    # Check concurrent keyword search limit (active + soft-deleted this month)
    concurrent_allowed, concurrent_count, concurrent_limit = SubscriptionService.check_usage_limit(
        user_id=current_user.id,
        metric_type="keyword_searches",
        db=db
    )
    
    # Check monthly creation limit
    monthly_allowed, monthly_count, monthly_limit = SubscriptionService.check_usage_limit(
        user_id=current_user.id,
        metric_type="keyword_searches_created_per_month",
        db=db
    )
    
    if not concurrent_allowed:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Maximum {concurrent_limit} keyword searches allowed (active + deleted this month). "
                   f"Currently using {concurrent_count}/{concurrent_limit}. "
                   f"Deleted searches count until the next month. Wait until next month or contact support."
        )
    
    if not monthly_allowed:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Maximum {monthly_limit} keyword searches can be created per month. "
                   f"You've created {monthly_count}/{monthly_limit} this month. "
                   f"Limit resets on the 1st of next month."
        )
    
    # Create keyword search
    keyword_search = KeywordSearch(
        user_id=current_user.id,
        name=search_data.name,
        keywords=search_data.keywords,
        patterns=search_data.patterns,
        subreddits=search_data.subreddits,
        platforms=search_data.platforms,
        enabled=search_data.enabled,
        scraping_mode=search_data.scraping_mode,
        scraping_interval=search_data.scraping_interval
    )
    
    db.add(keyword_search)
    db.commit()
    db.refresh(keyword_search)
    
    # Track monthly creation count
    UsageService.increment_usage(
        user_id=current_user.id,
        subscription_id=subscription.id,
        metric_type="keyword_searches_created_per_month",
        amount=1,
        db=db
    )
    
    # Create search in Rixly immediately (for reuse later)
    # This allows us to store rixly_search_id right away (stored in zola_search_id column for DB compatibility)
    try:
        rixly_search_id = await OpportunityService.create_keyword_search_in_rixly(keyword_search)
        keyword_search.zola_search_id = rixly_search_id  # Store in zola_search_id column for compatibility
        db.commit()
        db.refresh(keyword_search)
        logger.info(f"Created Rixly search for keyword_search {keyword_search.id}: {rixly_search_id}")
    except Exception as e:
        # Don't fail the request if Rixly is unavailable
        # We'll create it later when generating opportunities
        logger.warning(f"Failed to create search in Rixly (will retry on generate): {str(e)}")
        # Continue without rixly_search_id - it will be created when generating opportunities
    
    # Use Pydantic model to ensure only expected fields are returned
    return KeywordSearchResponse(
        id=keyword_search.id,
        name=keyword_search.name,
        keywords=keyword_search.keywords,
        patterns=keyword_search.patterns or [],
        subreddits=keyword_search.subreddits or [],
        platforms=keyword_search.platforms or ["reddit"],
        enabled=keyword_search.enabled,
        scraping_mode=getattr(keyword_search, 'scraping_mode', 'one_time'),  # Backward compatibility
        scraping_interval=getattr(keyword_search, 'scraping_interval', None),  # Backward compatibility
        created_at=keyword_search.created_at.isoformat() if keyword_search.created_at else "",
        updated_at=keyword_search.updated_at.isoformat() if keyword_search.updated_at else "",
    )


@router.get("/{search_id}", response_model=KeywordSearchResponse)
async def get_keyword_search(
    search_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get keyword search by ID.
    
    **Authentication Required**: Yes (JWT token)
    
    **Path Parameters**:
    - search_id: Keyword search UUID
    
    **Response 200**:
    - Keyword search details
    
    **Response 404**: Search not found or doesn't belong to user
    **Response 401**: Not authenticated
    """
    search = db.query(KeywordSearch).filter(
        KeywordSearch.id == search_id,
        KeywordSearch.user_id == current_user.id
    ).first()
    
    if not search:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword search not found"
        )
    
    # Use Pydantic model to ensure only expected fields are returned
    return KeywordSearchResponse(
        id=search.id,
        name=search.name,
        keywords=search.keywords,
        patterns=search.patterns or [],
        subreddits=search.subreddits or [],
        platforms=search.platforms or ["reddit"],
        enabled=search.enabled,
        scraping_mode=getattr(search, 'scraping_mode', 'one_time'),  # Backward compatibility
        scraping_interval=getattr(search, 'scraping_interval', None),  # Backward compatibility
        created_at=search.created_at.isoformat() if search.created_at else "",
        updated_at=search.updated_at.isoformat() if search.updated_at else "",
    )


@router.get("/{search_id}/leads-count")
async def get_keyword_search_leads_count(
    search_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get count of leads available in Rixly for this keyword search.
    
    This is useful for debugging - shows how many leads exist in Rixly
    that haven't been converted to opportunities yet.
    
    **Authentication Required**: Yes (JWT token)
    
    **Path Parameters**:
    - search_id: Keyword search UUID
    
    **Response 200**:
    ```json
    {
      "keyword_search_id": "uuid-here",
      "rixly_search_id": "search_07d9f986",
      "leads_count": 15,
      "has_rixly_search_id": true,
      "message": "Found 15 leads in Rixly"
    }
    ```
    
    **Response 404**: Search not found or doesn't belong to user
    """
    search = db.query(KeywordSearch).filter(
        KeywordSearch.id == search_id,
        KeywordSearch.user_id == current_user.id
    ).first()
    
    if not search:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword search not found"
        )
    
    rixly_search_id = search.zola_search_id  # Column name kept for compatibility, but stores Rixly ID
    
    if not rixly_search_id:
        return {
            "keyword_search_id": search.id,
            "rixly_search_id": None,
            "leads_count": 0,
            "has_rixly_search_id": False,
            "message": "No Rixly search ID linked. Generate opportunities to create the link."
        }
    
    # Fetch leads count from Rixly
    try:
        # Get total count by fetching all (with pagination)
        total_count = 0
        batch_size = 500
        offset = 0
        has_more = True
        
        while has_more:
            batch = await OpportunityService.fetch_leads_from_rixly(
                rixly_search_id=rixly_search_id,
                limit=batch_size,
                offset=offset
            )
            
            if batch:
                total_count += len(batch)
                offset += len(batch)
                has_more = len(batch) == batch_size
            else:
                has_more = False
        
        return {
            "keyword_search_id": search.id,
            "rixly_search_id": rixly_search_id,
            "leads_count": total_count,
            "has_rixly_search_id": True,
            "message": f"Found {total_count} leads in Rixly for search {rixly_search_id}"
        }
    except Exception as e:
        logger.error(f"Failed to fetch leads count from Rixly: {str(e)}")
        return {
            "keyword_search_id": search.id,
            "rixly_search_id": rixly_search_id,
            "leads_count": 0,
            "has_rixly_search_id": True,
            "message": f"Error fetching leads: {str(e)}"
        }


@router.post("/{search_id}/recreate-rixly-search")
async def recreate_rixly_search(
    search_id: str,
    current_user: User = Depends(get_current_user),
    subscription: Subscription = Depends(require_active_subscription),
    db: Session = Depends(get_db)
):
    """
    Recreate keyword search in Rixly if it's missing.
    
    This is useful when:
    - Leads exist in Rixly but the search was deleted
    - You want to re-enable scraping for an existing search
    - The rixly_search_id exists but the search doesn't in Rixly
    
    **Authentication Required**: Yes (JWT token)
    **Subscription Required**: Yes (active subscription)
    
    **Path Parameters**:
    - search_id: Keyword search UUID
    
    **Response 200**:
    ```json
    {
      "keyword_search_id": "uuid-here",
      "old_rixly_search_id": "search_07d9f986",
      "new_rixly_search_id": "search_abc123",
      "message": "Recreated search in Rixly"
    }
    ```
    
    **Response 404**: Search not found
    """
    search = db.query(KeywordSearch).filter(
        KeywordSearch.id == search_id,
        KeywordSearch.user_id == current_user.id
    ).first()
    
    if not search:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword search not found"
        )
    
    old_rixly_search_id = search.zola_search_id  # Column name kept for compatibility
    
    # Check if search exists in Rixly
    search_exists = False
    if old_rixly_search_id:
        search_exists = await OpportunityService.check_rixly_search_exists(old_rixly_search_id)
    
    if search_exists:
        return {
            "keyword_search_id": search.id,
            "old_rixly_search_id": old_rixly_search_id,
            "new_rixly_search_id": old_rixly_search_id,
            "message": f"Search already exists in Rixly: {old_rixly_search_id}"
        }
    
    # Create new search in Rixly
    try:
        logger.warning(
            f"Search {old_rixly_search_id} not found in Rixly. Creating new search. "
            f"Old leads will still be fetchable by old ID if they exist."
        )
        new_rixly_search_id = await OpportunityService.create_keyword_search_in_rixly(search)
        search.zola_search_id = new_rixly_search_id  # Store in zola_search_id column for compatibility
        db.commit()
        db.refresh(search)
        
        logger.info(
            f"Created new Rixly search for keyword_search {search.id}: "
            f"{old_rixly_search_id} -> {new_rixly_search_id}"
        )
        
        return {
            "keyword_search_id": search.id,
            "old_rixly_search_id": old_rixly_search_id,
            "new_rixly_search_id": new_rixly_search_id,
            "message": f"Created new search in Rixly. Old ID: {old_rixly_search_id}, New ID: {new_rixly_search_id}. Old leads still accessible by old ID if they exist."
        }
    except Exception as e:
        logger.error(f"Failed to recreate search in Rixly: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to recreate search in Rixly: {str(e)}"
        )


@router.put("/{search_id}", response_model=KeywordSearchResponse)
async def update_keyword_search(
    search_id: str,
    search_data: KeywordSearchUpdate,
    current_user: User = Depends(get_current_user),
    subscription: Subscription = Depends(require_active_subscription),
    db: Session = Depends(get_db)
):
    """
    Update keyword search.
    
    **Authentication Required**: Yes (JWT token)
    **Subscription Required**: Yes (active subscription)
    
    **Path Parameters**:
    - search_id: Keyword search UUID
    
    **Request Body**:
    - All fields optional (only provided fields will be updated)
    
    **Response 200**:
    - Updated keyword search
    
    **Response 404**: Search not found
    **Response 401**: Not authenticated
    """
    search = db.query(KeywordSearch).filter(
        KeywordSearch.id == search_id,
        KeywordSearch.user_id == current_user.id
    ).first()
    
    if not search:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword search not found"
        )
    
    # Validate keywords and subreddits limits if provided
    if search_data.keywords is not None:
        if len(search_data.keywords) > settings.MAX_KEYWORDS_PER_SEARCH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum {settings.MAX_KEYWORDS_PER_SEARCH} keywords allowed per search. "
                       f"Received {len(search_data.keywords)} keywords."
            )
    
    if search_data.subreddits is not None:
        if len(search_data.subreddits) > settings.MAX_SUBREDDITS_PER_SEARCH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum {settings.MAX_SUBREDDITS_PER_SEARCH} subreddits allowed per search. "
                       f"Received {len(search_data.subreddits)} subreddits."
            )
    
    # Validate scraping_mode if provided
    if search_data.scraping_mode is not None:
        if search_data.scraping_mode not in ["one_time", "scheduled"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scraping_mode must be 'one_time' or 'scheduled'"
            )
    
    # Validate scraping_interval if provided or if mode is being set to scheduled
    current_scraping_mode = getattr(search, 'scraping_mode', 'one_time')  # Backward compatibility
    current_scraping_interval = getattr(search, 'scraping_interval', None)  # Backward compatibility
    scraping_mode_to_use = search_data.scraping_mode if search_data.scraping_mode is not None else current_scraping_mode
    if scraping_mode_to_use == "scheduled":
        scraping_interval_to_use = search_data.scraping_interval if search_data.scraping_interval is not None else current_scraping_interval
        if not scraping_interval_to_use:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scraping_interval is required when scraping_mode is 'scheduled'"
            )
        if scraping_interval_to_use not in ["30m", "1h", "6h", "24h"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scraping_interval must be one of: '30m', '1h', '6h', '24h'"
            )
    elif search_data.scraping_mode == "one_time" and search_data.scraping_interval:
        # Clear interval if mode is being set to one_time
        search_data.scraping_interval = None
    
    # Update fields if provided
    if search_data.name is not None:
        search.name = search_data.name
    if search_data.keywords is not None:
        search.keywords = search_data.keywords
    if search_data.patterns is not None:
        search.patterns = search_data.patterns
    if search_data.subreddits is not None:
        search.subreddits = search_data.subreddits
    if search_data.platforms is not None:
        search.platforms = search_data.platforms
    if search_data.scraping_mode is not None:
        if hasattr(search, 'scraping_mode'):  # Backward compatibility
            search.scraping_mode = search_data.scraping_mode
    if search_data.scraping_interval is not None:
        if hasattr(search, 'scraping_interval'):  # Backward compatibility
            search.scraping_interval = search_data.scraping_interval
    elif search_data.scraping_mode == "one_time":
        # Clear interval if mode is being set to one_time
        if hasattr(search, 'scraping_interval'):  # Backward compatibility
            search.scraping_interval = None
    if search_data.enabled is not None:
        # Check limit if enabling (and not soft-deleted)
        if search_data.enabled and not search.enabled and search.deleted_at is None:
            allowed, current, limit = SubscriptionService.check_usage_limit(
                user_id=current_user.id,
                metric_type="keyword_searches",
                db=db
            )
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Cannot enable: Maximum {limit} keyword searches allowed (active + deleted this month). "
                           f"Currently using {current}/{limit}."
                )
        search.enabled = search_data.enabled
    
    # Ensure rixly_search_id exists and is valid (create/recreate if missing or deleted)
    # Note: Stored in zola_search_id column for database compatibility
    if not search.zola_search_id:
        # Create new search in Rixly
        try:
            rixly_search_id = await OpportunityService.create_keyword_search_in_rixly(search)
            search.zola_search_id = rixly_search_id  # Store in zola_search_id column
            db.commit()
            db.refresh(search)
            logger.info(f"Created Rixly search for keyword_search {search.id}: {rixly_search_id}")
        except Exception as e:
            # Don't fail the request if Rixly is unavailable
            logger.warning(f"Failed to create search in Rixly (will retry on generate): {str(e)}")
    else:
        # Check if search exists in Rixly, recreate if missing
        search_exists = await OpportunityService.check_rixly_search_exists(search.zola_search_id)
        if not search_exists:
            # Search doesn't exist - need to recreate
            logger.warning(
                f"Rixly search {search.zola_search_id} does not exist. "
                f"Creating new search. Old leads will still be fetchable by old ID if they exist."
            )
            try:
                new_rixly_search_id = await OpportunityService.create_keyword_search_in_rixly(search)
                search.zola_search_id = new_rixly_search_id
                db.commit()
                db.refresh(search)
                logger.info(
                    f"Created new Rixly search for keyword_search {search.id}: "
                    f"{search.zola_search_id} -> {new_rixly_search_id} (name: {search.name})"
                )
            except Exception as e:
                logger.warning(f"Failed to recreate search in Rixly: {str(e)}")
    
    db.commit()
    db.refresh(search)
    
    # Use Pydantic model to ensure only expected fields are returned
    return KeywordSearchResponse(
        id=search.id,
        name=search.name,
        keywords=search.keywords,
        patterns=search.patterns or [],
        subreddits=search.subreddits or [],
        platforms=search.platforms or ["reddit"],
        enabled=search.enabled,
        scraping_mode=getattr(search, 'scraping_mode', 'one_time'),  # Backward compatibility
        scraping_interval=getattr(search, 'scraping_interval', None),  # Backward compatibility
        created_at=search.created_at.isoformat() if search.created_at else "",
        updated_at=search.updated_at.isoformat() if search.updated_at else "",
    )


@router.delete("/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_keyword_search(
    search_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Soft delete keyword search (marks as deleted but keeps in database).
    
    IMPORTANT: Soft-deleted searches still count toward limit until next month.
    This prevents abuse where users create/delete searches repeatedly.
    
    **Authentication Required**: Yes (JWT token)
    
    **Path Parameters**:
    - search_id: Keyword search UUID
    
    **Response 204**: Successfully soft-deleted
    
    **Response 404**: Search not found
    **Response 401**: Not authenticated
    """
    search = db.query(KeywordSearch).filter(
        KeywordSearch.id == search_id,
        KeywordSearch.user_id == current_user.id,
        KeywordSearch.deleted_at.is_(None)  # Only allow deleting non-deleted searches
    ).first()
    
    if not search:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword search not found or already deleted"
        )
    
    # Soft delete: mark as deleted but keep in database
    # This prevents abuse: deleted searches still count toward limit until next month
    search.soft_delete()
    search.enabled = False  # Also disable it
    db.commit()
    
    logger.info(
        f"Soft-deleted keyword search {search_id} for user {current_user.id}. "
        f"Search will still count toward limit until next month."
    )
    
    return None
