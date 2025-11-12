"""
Opportunity Service

Handles opportunity generation by integrating with Rixly API.
Converts Rixly "leads" to SaaS "opportunities" with user isolation.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
import httpx
import asyncio

from models.user import User
from models.subscription import Subscription
from models.keyword_search import KeywordSearch
from models.opportunity import Opportunity, OpportunityStatus
from services.subscription_service import SubscriptionService
from services.usage_service import UsageService
from core.config import get_settings
from core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class OpportunityService:
    """Service for generating opportunities from Rixly API."""
    
    @staticmethod
    def get_rixly_api_url() -> str:
        """
        Get Rixly API base URL.
        
        Returns:
            str: Rixly API URL
        """
        return settings.RIXLY_API_URL.rstrip('/')
    
    @staticmethod
    def get_rixly_headers() -> Dict[str, str]:
        """
        Get headers for Rixly API requests.
        
        Returns:
            dict: Headers with API key
        """
        api_key = settings.RIXLY_API_KEY or "dev_api_key"  # Default for development
        
        return {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }
    
    @staticmethod
    async def get_rixly_search(rixly_search_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a keyword search from Rixly API.
        
        Args:
            rixly_search_id: Rixly keyword search ID
            
        Returns:
            dict: Search data or None if not found
        """
        api_url = OpportunityService.get_rixly_api_url()
        headers = OpportunityService.get_rixly_headers()
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{api_url}/api/v1/keyword-searches/{rixly_search_id}",
                    headers=headers
                )
                if response.status_code == 200:
                    return response.json()
                return None
        except Exception:
            return None
    
    @staticmethod
    async def create_keyword_search_in_rixly(
        keyword_search: KeywordSearch
    ) -> str:
        """
        Create a keyword search in Rixly API.
        
        Args:
            keyword_search: KeywordSearch model from SaaS database
            
        Returns:
            str: Rixly search ID
            
        Raises:
            HTTPException: If API call fails
        """
        api_url = OpportunityService.get_rixly_api_url()
        headers = OpportunityService.get_rixly_headers()
        
        # Prepare payload for Rixly API
        # Rixly expects reddit_config with subreddits array
        subreddits = keyword_search.subreddits or ["forhire", "hiring", "freelance"]
        payload = {
            "name": keyword_search.name,
            "keywords": keyword_search.keywords,
            "patterns": keyword_search.patterns or ["looking for", "need", "hiring", "want"],
            "platforms": keyword_search.platforms or ["reddit"],
            "reddit_config": {
                "subreddits": subreddits,
                "limit": 100,
                "include_comments": True,
                "sort": "new",
                "time_filter": "day"
            },
            "scraping_mode": "one_time",  # Always use one_time for on-demand scraping
            "enabled": keyword_search.enabled
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{api_url}/api/v1/keyword-searches",
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
                
                rixly_search_id = data.get("id")
                if not rixly_search_id:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Invalid response from Rixly API: missing search ID"
                    )
                
                logger.info(f"Created keyword search in Rixly: {rixly_search_id}")
                return rixly_search_id
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Rixly API error: {e.response.text}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Rixly API error: {e.response.text}"
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to Rixly: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to connect to Rixly API: {str(e)}"
            )
    
    @staticmethod
    async def check_rixly_search_exists(rixly_search_id: str) -> bool:
        """
        Check if a keyword search exists in Rixly.
        
        Args:
            rixly_search_id: Rixly keyword search ID
            
        Returns:
            bool: True if search exists, False otherwise
        """
        api_url = OpportunityService.get_rixly_api_url()
        headers = OpportunityService.get_rixly_headers()
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{api_url}/api/v1/keyword-searches/{rixly_search_id}",
                    headers=headers
                )
                return response.status_code == 200
        except Exception:
            return False
    
    @staticmethod
    async def check_rixly_scrape_status(rixly_search_id: str) -> Dict[str, Any]:
        """
        Check scraping status for a keyword search in Rixly.
        
        Args:
            rixly_search_id: Rixly keyword search ID
            
        Returns:
            dict: Status information (status, started_at, completed_at, error)
        """
        api_url = OpportunityService.get_rixly_api_url()
        headers = OpportunityService.get_rixly_headers()
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Rixly uses /status instead of /scrape-status
                response = await client.get(
                    f"{api_url}/api/v1/keyword-searches/{rixly_search_id}/status",
                    headers=headers
                )
                if response.status_code == 200:
                    data = response.json()
                    # Map Rixly response format to expected format
                    # Rixly returns: {scraping_status, job_info: {status, started_at, ...}, ...}
                    status_info = {
                        "status": data.get("scraping_status", "idle"),
                        "started_at": None,
                        "completed_at": None,
                        "error": None
                    }
                    
                    # Extract from job_info if available
                    job_info = data.get("job_info", {})
                    if job_info:
                        status_info["status"] = job_info.get("status", status_info["status"])
                        status_info["started_at"] = job_info.get("started_at")
                        status_info["completed_at"] = job_info.get("completed_at")
                        status_info["error"] = job_info.get("error")
                    
                    # Also check direct fields
                    if data.get("scraping_started_at"):
                        status_info["started_at"] = data.get("scraping_started_at")
                    if data.get("scraping_completed_at"):
                        status_info["completed_at"] = data.get("scraping_completed_at")
                    if data.get("scraping_error"):
                        status_info["error"] = data.get("scraping_error")
                    
                    # Extract cooldown information for auto-refresh logic
                    status_info["last_scrape_at"] = data.get("last_scrape_at")
                    status_info["time_since_last_minutes"] = data.get("time_since_last_minutes")
                    status_info["cooldown_remaining"] = data.get("cooldown_remaining")
                    
                    return status_info
                return {"status": "idle"}
        except Exception as e:
            logger.warning(f"Failed to check scrape status: {str(e)}")
            return {"status": "unknown"}
    
    @staticmethod
    async def trigger_rixly_scrape(rixly_search_id: str) -> None:
        """
        Trigger on-demand scraping for a keyword search in Rixly.
        
        First checks if scraping is already in progress to avoid duplicate jobs.
        
        Args:
            rixly_search_id: Rixly keyword search ID
            
        Raises:
            HTTPException: If API call fails (but doesn't raise - just logs warning)
        """
        api_url = OpportunityService.get_rixly_api_url()
        headers = OpportunityService.get_rixly_headers()
        
        # First check if search exists
        search_exists = await OpportunityService.check_rixly_search_exists(rixly_search_id)
        if not search_exists:
            logger.warning(
                f"Keyword search {rixly_search_id} does not exist in Rixly. "
                f"Leads may exist but search was deleted. Skipping scrape trigger."
            )
            return
        
        # Check if scraping is already in progress
        scrape_status = await OpportunityService.check_rixly_scrape_status(rixly_search_id)
        status = scrape_status.get("status", "idle")
        if status in ["processing", "running"]:
            logger.info(
                f"Scraping already in progress for {rixly_search_id}. "
                f"Started at: {scrape_status.get('started_at')}. Skipping new scrape trigger."
            )
            return
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{api_url}/api/v1/keyword-searches/{rixly_search_id}/scrape",
                    headers=headers
                )
                if response.status_code == 409:
                    # Cooldown or conflict - raise HTTPException so caller can handle it
                    error_detail = "Unknown conflict"
                    try:
                        error_data = response.json()
                        error_detail = error_data.get("detail", error_data.get("message", "Cooldown period not met or scraping already in progress"))
                    except:
                        error_detail = response.text or "Cooldown period not met or scraping already in progress"
                    
                    logger.info(f"Cannot trigger scrape (cooldown/conflict) for Rixly search {rixly_search_id}: {error_detail}")
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=error_detail
                    )
                response.raise_for_status()
                logger.info(f"Successfully triggered scrape for Rixly search: {rixly_search_id}")
        except HTTPException:
            # Re-raise HTTPException (including 409) so caller can handle it
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(
                    f"Keyword search {rixly_search_id} not found in Rixly. "
                    f"This may happen if the search was deleted but leads still exist. "
                    f"Continuing to fetch existing leads."
                )
            elif e.response.status_code == 409:
                # Cooldown or conflict - extract error message and raise HTTPException
                error_detail = "Cooldown period not met or scraping already in progress"
                try:
                    error_data = e.response.json()
                    error_detail = error_data.get("detail", error_data.get("message", error_detail))
                except:
                    error_detail = e.response.text or error_detail
                
                logger.info(f"Cannot trigger scrape (cooldown/conflict) for Rixly search {rixly_search_id}: {error_detail}")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=error_detail
                )
            else:
                logger.warning(f"Failed to trigger scrape: {e.response.text}")
                # For other errors, don't raise - might have existing leads
        except httpx.RequestError as e:
            logger.warning(f"Failed to connect to trigger scrape: {str(e)}")
            # Don't raise - might have existing leads
    
    @staticmethod
    async def fetch_leads_from_rixly(
        rixly_search_id: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Fetch leads from Rixly API for a keyword search.
        
        Args:
            rixly_search_id: Rixly keyword search ID
            limit: Maximum number of leads to fetch
            offset: Pagination offset
            
        Returns:
            list: List of lead dictionaries from Rixly
            
        Raises:
            HTTPException: If API call fails
        """
        api_url = OpportunityService.get_rixly_api_url()
        headers = OpportunityService.get_rixly_headers()
        
        params = {
            "keyword_search_id": rixly_search_id,
            "limit": limit,
            "offset": offset
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(
                    f"{api_url}/api/v1/leads",
                    headers=headers,
                    params=params
                )
                response.raise_for_status()
                data = response.json()
                
                # Rixly returns paginated response: {items: [...], total, limit, offset, has_more}
                if isinstance(data, dict) and "items" in data:
                    leads = data["items"]
                elif isinstance(data, list):
                    leads = data
                elif isinstance(data, dict) and "leads" in data:
                    leads = data["leads"]
                else:
                    leads = []
                
                logger.info(f"Fetched {len(leads)} leads from Rixly for search {rixly_search_id}")
                return leads
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Rixly API error: {e.response.text}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Rixly API error: {e.response.text}"
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to Rixly: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to connect to Rixly API: {str(e)}"
            )
    
    @staticmethod
    def convert_zola_lead_to_opportunity(
        zola_lead: Dict[str, Any],
        user_id: str,
        keyword_search_id: str
    ) -> Opportunity:
        """
        Convert a Rixly lead to SaaS opportunity.
        
        Note: Function name kept for backward compatibility, but now receives Rixly leads.
        
        Args:
            zola_lead: Lead dictionary from Rixly API
            user_id: User UUID (for multi-tenancy)
            keyword_search_id: Keyword search UUID
            
        Returns:
            Opportunity: Opportunity model instance
        """
        # Map Rixly lead fields to SaaS opportunity fields
        # Handle different possible field names from Rixly
        source_post_id = zola_lead.get("source_id") or zola_lead.get("source_post_id") or zola_lead.get("id", "")
        source = zola_lead.get("source", "reddit")
        source_type = zola_lead.get("source_type", "post")
        
        opportunity = Opportunity(
            user_id=user_id,
            keyword_search_id=keyword_search_id,
            source_post_id=source_post_id,
            source=source,
            source_type=source_type,
            title=zola_lead.get("title"),
            content=zola_lead.get("content") or zola_lead.get("text", ""),
            author=zola_lead.get("author") or zola_lead.get("username", "unknown"),
            url=zola_lead.get("url") or zola_lead.get("link", ""),
            matched_keywords=zola_lead.get("matched_keywords") or zola_lead.get("keywords", []),
            detected_pattern=zola_lead.get("detected_pattern") or zola_lead.get("pattern"),
            opportunity_type=zola_lead.get("opportunity_type") or zola_lead.get("type"),
            opportunity_subtype=zola_lead.get("opportunity_subtype") or zola_lead.get("subtype"),
            relevance_score=float(zola_lead.get("relevance_score", 0.0)),
            urgency_score=float(zola_lead.get("urgency_score", 0.0)),
            total_score=float(zola_lead.get("total_score") or zola_lead.get("score", 0.0)),
            extracted_info=zola_lead.get("extracted_info") or zola_lead.get("extracted_data"),
            status=OpportunityStatus.NEW
        )
        
        return opportunity
    
    @staticmethod
    async def generate_opportunities(
        keyword_search_id: str,
        user_id: str,
        subscription_id: str,
        db: Session,
        limit: int = 100,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Generate opportunities from Rixly API for a keyword search.
        
        This method:
        1. Creates keyword search in Rixly (or reuses existing)
        2. Triggers scraping in Rixly (if needed, respects force_refresh)
        3. Fetches leads from Rixly
        4. Converts leads to opportunities (with user_id)
        5. Handles deduplication (same source_post_id for same user)
        6. Increments usage metrics
        
        Args:
            keyword_search_id: Keyword search UUID
            user_id: User UUID
            subscription_id: Subscription UUID
            db: Database session
            limit: Maximum number of opportunities to generate
            force_refresh: If True, force new scrape even if leads exist (default: False)
            
        Returns:
            dict: Result with count and opportunities
            
        Raises:
            HTTPException: If keyword search not found, limit reached, or API errors
        """
        # Get keyword search
        keyword_search = db.query(KeywordSearch).filter(
            KeywordSearch.id == keyword_search_id,
            KeywordSearch.user_id == user_id
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
        
        # Check opportunity limit (monthly)
        allowed, current, limit_count = SubscriptionService.check_usage_limit(
            user_id=user_id,
            metric_type="opportunities_per_month",
            db=db
        )
        
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Monthly opportunity limit reached ({current}/{limit_count}). "
                       f"Upgrade your plan or wait for the next billing period."
            )
        
        # Step 1: Get or create keyword search in Rixly
        # Reuse existing rixly_search_id if available (stored in zola_search_id column for backward compatibility)
        rixly_search_id = keyword_search.zola_search_id  # Keep column name for DB compatibility
        
        if not rixly_search_id:
            # Create new search in Rixly
            try:
                rixly_search_id = await OpportunityService.create_keyword_search_in_rixly(keyword_search)
                # Store rixly_search_id for future reuse (using zola_search_id column)
                keyword_search.zola_search_id = rixly_search_id
                db.commit()
                logger.info(f"Created and stored Rixly search ID: {rixly_search_id}")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to create search in Rixly: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to create search in Rixly: {str(e)}"
                )
        else:
            logger.info(f"Reusing existing Rixly search ID: {rixly_search_id}")
        
        # Step 2: Check scrape status and determine if we should refresh
        # For one-time searches, we should auto-refresh after cooldown passes
        scrape_status = await OpportunityService.check_rixly_scrape_status(rixly_search_id)
        status_value = scrape_status.get("status", "idle")
        is_scraping = status_value in ["processing", "running"]
        
        # Get cooldown information
        last_scrape_at = scrape_status.get("last_scrape_at")
        time_since_last_minutes = scrape_status.get("time_since_last_minutes")
        COOLDOWN_MINUTES = 10  # Rixly default cooldown period
        
        # Determine if cooldown has passed
        cooldown_passed = False
        if time_since_last_minutes is not None:
            cooldown_passed = time_since_last_minutes >= COOLDOWN_MINUTES
        elif not last_scrape_at:
            # Never scraped - should scrape
            cooldown_passed = True
        
        # Step 3: Check if leads exist (only if not forcing refresh)
        existing_leads_count = 0
        should_attempt_refresh = False
        cooldown_message = None  # Store cooldown message for frontend
        
        logger.info(
            f"force_refresh={force_refresh}, cooldown_passed={cooldown_passed}, "
            f"time_since_last={time_since_last_minutes} min, last_scrape_at={last_scrape_at}"
        )
        
        if force_refresh:
            # Force refresh - always attempt to trigger scrape
            should_attempt_refresh = True
            logger.info(
                f"force_refresh=True: Will attempt to trigger new scrape for {rixly_search_id} "
                f"(respects cooldown period)"
            )
        elif cooldown_passed:
            # Cooldown passed - automatically attempt refresh for one-time searches
            # This allows users to get fresh leads without explicitly using force_refresh
            should_attempt_refresh = True
            logger.info(
                f"Cooldown passed ({time_since_last_minutes:.1f} min since last scrape). "
                f"Will automatically attempt to refresh leads for {rixly_search_id}."
            )
        else:
            # Cooldown still active - check if leads exist to use them
            try:
                existing_leads = await OpportunityService.fetch_leads_from_rixly(
                    rixly_search_id=rixly_search_id,
                    limit=1,  # Just check if any exist
                    offset=0
                )
                existing_leads_count = len(existing_leads) if existing_leads else 0
                if existing_leads_count > 0:
                    logger.info(
                        f"Found {existing_leads_count}+ existing leads for {rixly_search_id}. "
                        f"Cooldown still active ({time_since_last_minutes:.1f} min). "
                        f"Will use existing leads (fast response)."
                    )
                else:
                    # No leads exist - should scrape
                    should_attempt_refresh = True
                    logger.info(
                        f"No existing leads found for {rixly_search_id}. "
                        f"Will trigger scrape."
                    )
            except Exception as e:
                logger.debug(f"Could not check existing leads (will try to scrape): {str(e)}")
                should_attempt_refresh = True
        
        # Step 4: Check if scraping is already in progress
        if is_scraping:
            logger.info(
                f"Scraping already in progress for {rixly_search_id}. "
                f"Started at: {scrape_status.get('started_at')}. "
                f"Will wait for it to complete before fetching leads."
            )
        elif should_attempt_refresh or existing_leads_count == 0:
            # Trigger scrape if:
            # 1. force_refresh=True (user wants fresh leads immediately)
            # 2. OR cooldown passed (auto-refresh for one-time searches - get fresh leads)
            # 3. OR no leads exist (first time or leads were deleted)
            try:
                await OpportunityService.trigger_rixly_scrape(rixly_search_id)
                logger.info(
                    f"Triggered scrape for Rixly search: {rixly_search_id} "
                    f"(force_refresh={force_refresh}, existing_leads={existing_leads_count})"
                )
                is_scraping = True  # Assume scraping started
            except HTTPException as e:
                if e.status_code == 409:  # Conflict (cooldown or already running)
                    cooldown_message = e.detail or "Cooldown period not met"
                    logger.info(
                        f"Cannot trigger scrape (cooldown or conflict): {cooldown_message}. "
                        f"Will use existing leads if available."
                    )
                    is_scraping = False
                    # Store cooldown message to include in result for frontend
                    # Don't re-raise - we want to continue and use existing leads
                else:
                    logger.warning(f"Failed to trigger scrape: {e.detail}")
                    # Don't fail - might have existing leads
            except Exception as e:
                logger.warning(f"Failed to trigger scrape (continuing anyway): {str(e)}")
                # Don't fail if scrape trigger fails - might have existing leads
        else:
            logger.info(
                f"Skipping scrape trigger for {rixly_search_id} - existing leads found "
                f"and cooldown still active ({time_since_last_minutes:.1f} min). "
                f"Will use existing leads for fast response."
            )
        
        # Step 5: Wait for scraping to complete (or poll for results)
        # Scraping can take 15-60+ seconds (Reddit API + AI analysis)
        if is_scraping:
            # Poll for scraping completion
            max_wait_time = 120  # 2 minutes max wait
            poll_interval = 5  # Check every 5 seconds
            waited = 0
            
            while waited < max_wait_time:
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                
                status = await OpportunityService.check_rixly_scrape_status(rixly_search_id)
                current_status = status.get("status", "idle")
                
                if current_status in ["completed", "idle"]:
                    logger.info(f"Scraping completed for {rixly_search_id}")
                    break
                elif current_status == "failed":
                    logger.warning(f"Scraping failed for {rixly_search_id}: {status.get('error')}")
                    break
                elif current_status in ["processing", "running"]:
                    # Still processing, continue waiting
                    logger.debug(f"Scraping still in progress for {rixly_search_id} ({waited}s elapsed)")
                    continue
                else:
                    # Unknown status - might have completed
                    logger.info(f"Scraping status: {current_status} for {rixly_search_id}")
                    break
        else:
            # No scraping triggered, wait a bit for any existing scraping to progress
            await asyncio.sleep(5)
        
        # Step 6: Fetch ALL leads from Rixly (with retry logic)
        # Fetch all leads - deduplication will handle skipping existing opportunities
        # This ensures we get both new and old leads, but only create new opportunities
        max_retries = 10
        retry_delay = 5  # seconds
        rixly_leads = []
        
        for attempt in range(max_retries):
            try:
                # Fetch with higher limit to get all leads (deduplication handles existing ones)
                # Fetch in batches to get all leads, not just the limit
                all_leads = []
                batch_size = 500  # Rixly API max limit
                offset = 0
                has_more = True
                
                while has_more:
                    batch = await OpportunityService.fetch_leads_from_rixly(
                        rixly_search_id=rixly_search_id,
                        limit=batch_size,
                        offset=offset
                    )
                    
                    if batch:
                        all_leads.extend(batch)
                        offset += len(batch)
                        # If we got fewer than batch_size, we've reached the end
                        has_more = len(batch) == batch_size
                    else:
                        has_more = False
                
                if all_leads:
                    rixly_leads = all_leads
                    logger.info(f"Fetched {len(rixly_leads)} total leads from Rixly (attempt {attempt + 1})")
                    break
                elif attempt < max_retries - 1:
                    logger.info(f"No leads yet, waiting {retry_delay}s before retry {attempt + 2}/{max_retries}")
                    await asyncio.sleep(retry_delay)
            except HTTPException:
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Failed to fetch leads (attempt {attempt + 1}/{max_retries}): {str(e)}")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"Failed to fetch leads from Rixly after {max_retries} attempts: {str(e)}")
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Failed to fetch leads from Rixly: {str(e)}"
                    )
        
        if not rixly_leads:
            logger.warning(f"No leads found from Rixly for search {rixly_search_id} after {max_retries} attempts")
            return {
                "opportunities_created": 0,
                "opportunities_skipped": 0,
                "opportunities": [],
                "message": "No leads found from Reddit scraping. Try adjusting your keywords or subreddits."
            }
        
        # Step 5: Convert Rixly leads to SaaS opportunities (with user_id)
        # First, extract all source_post_ids and batch-check for existing opportunities
        # This is much more efficient than checking one-by-one
        source_post_ids = []
        valid_leads = []
        
        for rixly_lead in rixly_leads:
            source_post_id = rixly_lead.get("source_id") or rixly_lead.get("source_post_id") or rixly_lead.get("id", "")
            
            if not source_post_id:
                logger.warning(f"Skipping lead without source_id: {rixly_lead}")
                continue
            
            source_post_ids.append(source_post_id)
            valid_leads.append((source_post_id, rixly_lead))
        
        # Batch check for existing opportunities (single query instead of N queries)
        existing_opportunities = set()
        if source_post_ids:
            existing = db.query(Opportunity.source_post_id).filter(
                Opportunity.user_id == user_id,
                Opportunity.source_post_id.in_(source_post_ids)
            ).all()
            existing_opportunities = {row[0] for row in existing}
            logger.info(
                f"Found {len(existing_opportunities)} existing opportunities out of {len(source_post_ids)} leads"
            )
        
        # Process only new leads (not already converted)
        opportunities_created = []
        opportunities_skipped = 0
        
        for source_post_id, rixly_lead in valid_leads:
            # Skip if already converted to opportunity for this user
            if source_post_id in existing_opportunities:
                opportunities_skipped += 1
                continue
            
            # Create new opportunity
            try:
                opportunity = OpportunityService.convert_zola_lead_to_opportunity(
                    zola_lead=rixly_lead,  # Function name kept for compatibility, but receives Rixly lead
                    user_id=user_id,
                    keyword_search_id=keyword_search_id
                )
                db.add(opportunity)
                opportunities_created.append(opportunity)
            except Exception as e:
                logger.error(f"Failed to convert lead to opportunity: {str(e)}")
                opportunities_skipped += 1
                continue
        
        # Commit all opportunities
        db.commit()
        
        # Update keyword search last_run_at
        keyword_search.last_run_at = datetime.utcnow()
        db.commit()
        
        # Step 6: Increment monthly usage
        if opportunities_created:
            UsageService.increment_usage(
                user_id=user_id,
                subscription_id=subscription_id,
                metric_type="opportunities_per_month",
                amount=len(opportunities_created),
                db=db
            )
        
        logger.info(
            f"Generated {len(opportunities_created)} opportunities for user {user_id}, "
            f"skipped {opportunities_skipped} duplicates"
        )
        
        result = {
            "opportunities_created": len(opportunities_created),
            "opportunities_skipped": opportunities_skipped,
            "opportunities": [opp.to_dict() for opp in opportunities_created],
            "message": f"Successfully generated {len(opportunities_created)} new opportunities"
        }
        
        # Include cooldown message if one occurred (for frontend display)
        if cooldown_message:
            result["cooldown_message"] = cooldown_message
            result["message"] = f"Generated {len(opportunities_created)} opportunities from existing leads. {cooldown_message}"
        
        return result

