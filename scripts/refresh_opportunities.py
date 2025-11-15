#!/usr/bin/env python3
"""
Manual Opportunity Refresh Script

This script manually fetches leads from Rixly API and creates opportunities.
This is an alternative to webhooks - useful for:
- Testing
- Manual refresh when webhooks fail
- One-time searches that don't use webhooks
- Debugging

Usage:
    # Refresh for a specific user (by email)
    python scripts/refresh_opportunities.py --user-email user@example.com

    # Refresh for a specific user (by user ID)
    python scripts/refresh_opportunities.py --user-id <uuid>

    # Refresh for a specific keyword search
    python scripts/refresh_opportunities.py --search-id <uuid>

    # Refresh for all users with active searches
    python scripts/refresh_opportunities.py --all-users

    # Refresh without sending emails
    python scripts/refresh_opportunities.py --user-email user@example.com --no-email

    # Refresh scheduled searches too (normally only one_time searches are refreshed)
    python scripts/refresh_opportunities.py --user-email user@example.com --include-scheduled
"""

import os
import sys
import argparse
import asyncio
from typing import Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import SessionLocal
from core.logger import get_logger, setup_logging
from core.config import get_settings
from models.user import User
from models.keyword_search import KeywordSearch
from services.lead_refresh_service import LeadRefreshService
from services.opportunity_service import OpportunityService

# Initialize logging
setup_logging()
logger = get_logger(__name__)
settings = get_settings()


async def refresh_for_user(
    user_email: Optional[str] = None,
    user_id: Optional[str] = None,
    send_email: bool = True,
    include_scheduled: bool = False
) -> dict:
    """
    Refresh opportunities for a specific user.
    
    Args:
        user_email: User email address
        user_id: User UUID
        send_email: Whether to send email notifications
        include_scheduled: Whether to include scheduled searches (normally only one_time)
        
    Returns:
        dict: Refresh results
    """
    db = SessionLocal()
    try:
        # Find user
        if user_email:
            user = db.query(User).filter(User.email == user_email).first()
            if not user:
                return {
                    "status": "error",
                    "message": f"User with email {user_email} not found"
                }
        elif user_id:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return {
                    "status": "error",
                    "message": f"User with ID {user_id} not found"
                }
        else:
            return {
                "status": "error",
                "message": "Either user_email or user_id must be provided"
            }
        
        logger.info(f"Refreshing opportunities for user: {user.email} (ID: {user.id})")
        
        # Get all searches for debugging
        all_searches = db.query(KeywordSearch).filter(
            KeywordSearch.user_id == user.id,
            KeywordSearch.deleted_at.is_(None)  # type: ignore
        ).all()
        
        logger.info(f"Found {len(all_searches)} total active searches for user {user.email}")
        
        # Debug: Log search details
        for search in all_searches:
            logger.info(
                f"  Search: {search.name} (ID: {search.id}) - "
                f"scraping_mode: {search.scraping_mode}, "
                f"zola_search_id: {search.zola_search_id}, "
                f"deleted_at: {search.deleted_at}"
            )
        
        # Get active searches with Rixly integration
        query = db.query(KeywordSearch).filter(
            KeywordSearch.user_id == user.id,
            KeywordSearch.deleted_at.is_(None),  # type: ignore
            KeywordSearch.zola_search_id.isnot(None)  # type: ignore - zola_search_id stores Rixly search ID
        )
        
        # Filter by scraping mode if not including scheduled
        if not include_scheduled:
            query = query.filter(KeywordSearch.scraping_mode == "one_time")
            logger.info("Filtering for one_time searches only (use --include-scheduled to include scheduled searches)")
        else:
            logger.info("Including both one_time and scheduled searches")
        
        active_searches = query.all()
        
        if not active_searches:
            # Provide helpful error message
            searches_without_rixly = [s for s in all_searches if not s.zola_search_id]
            scheduled_searches = [s for s in all_searches if s.scraping_mode == "scheduled" and s.zola_search_id]
            
            message = "No active searches with Rixly integration found"
            if scheduled_searches and not include_scheduled:
                message += f". Found {len(scheduled_searches)} scheduled search(es) - use --include-scheduled to refresh them"
            if searches_without_rixly:
                message += f". Found {len(searches_without_rixly)} search(es) without Rixly integration (missing zola_search_id)"
            
            return {
                "status": "success",
                "user_id": user.id,
                "user_email": user.email,
                "searches_checked": 0,
                "new_opportunities": 0,
                "message": message,
                "debug_info": {
                    "total_searches": len(all_searches),
                    "searches_without_rixly": len(searches_without_rixly),
                    "scheduled_searches": len(scheduled_searches) if not include_scheduled else 0
                }
            }
        
        logger.info(f"Found {len(active_searches)} active searches for user {user.email}")
        
        # Refresh leads for each search
        total_new_opportunities = 0
        searches_with_new_leads = []
        
        for search in active_searches:
            try:
                logger.info(f"Fetching leads for search: {search.name} (Rixly ID: {search.zola_search_id})")
                
                # Fetch leads from Rixly
                leads = await OpportunityService.fetch_leads_from_rixly(
                    rixly_search_id=search.zola_search_id,  # type: ignore
                    limit=500,  # Fetch more leads
                    offset=0
                )
                
                if not leads:
                    logger.info(f"No leads found for search {search.name}")
                    continue
                
                logger.info(f"Fetched {len(leads)} leads from Rixly for search {search.name}")
                
                # Extract source_post_ids from leads (batch check is more efficient)
                # Note: source_post_id stores the Rixly lead ID (source_id from Rixly API)
                source_post_ids = []
                valid_leads = []
                
                for lead in leads:
                    source_post_id = lead.get("source_id") or lead.get("source_post_id") or lead.get("id", "")
                    if not source_post_id:
                        logger.warning(f"Skipping lead without source_id: {lead.get('title', 'Unknown')}")
                        continue
                    source_post_ids.append(source_post_id)
                    valid_leads.append((source_post_id, lead))
                
                # Batch check for existing opportunities (more efficient than checking one-by-one)
                # IMPORTANT: Check by user_id, not just keyword_search_id, because UniqueConstraint
                # is on (user_id, source_post_id) - same lead can't exist twice for same user
                from models.opportunity import Opportunity
                from sqlalchemy.exc import IntegrityError
                
                existing_source_ids = set()
                if source_post_ids:
                    existing = db.query(Opportunity.source_post_id).filter(
                        Opportunity.user_id == user.id,
                        Opportunity.source_post_id.in_(source_post_ids)
                    ).all()
                    existing_source_ids = {row[0] for row in existing}
                    logger.info(
                        f"Found {len(existing_source_ids)} existing opportunities out of {len(source_post_ids)} leads"
                    )
                
                # Process new leads
                new_count = 0
                for source_post_id, lead in valid_leads:
                    if source_post_id in existing_source_ids:
                        continue
                    
                    # Convert lead to opportunity
                    try:
                        opportunity = OpportunityService.convert_zola_lead_to_opportunity(
                            zola_lead=lead,
                            user_id=user.id,
                            keyword_search_id=search.id
                        )
                        db.add(opportunity)
                        new_count += 1
                        total_new_opportunities += 1
                    except IntegrityError as e:
                        # Handle database constraint violation (duplicate)
                        db.rollback()
                        logger.warning(f"Duplicate opportunity detected (constraint violation): {source_post_id}")
                        continue
                    except Exception as e:
                        logger.error(f"Error converting lead to opportunity: {str(e)}")
                        db.rollback()
                        continue
                
                if new_count > 0:
                    db.commit()
                    searches_with_new_leads.append({
                        "search_id": search.id,
                        "search_name": search.name,
                        "new_count": new_count
                    })
                    logger.info(f"Created {new_count} new opportunities for search {search.name}")
                else:
                    logger.info(f"No new opportunities for search {search.name} (all leads already exist)")
                    
            except Exception as e:
                logger.error(f"Error refreshing leads for search {search.id}: {str(e)}", exc_info=True)
                db.rollback()
                continue
        
        # Send email notification if requested
        email_sent = False
        if send_email and total_new_opportunities > 0 and user.email_notifications_enabled:
            try:
                # Use the service method to send email
                await asyncio.to_thread(
                    LeadRefreshService._send_new_leads_email_sync,
                    user=user,
                    new_opportunities_count=total_new_opportunities,
                    searches_with_leads=searches_with_new_leads,
                    db=db
                )
                email_sent = True
                logger.info(f"Sent email notification to {user.email}")
            except Exception as e:
                logger.error(f"Error sending email notification: {str(e)}")
        
        return {
            "status": "success",
            "user_id": user.id,
            "user_email": user.email,
            "searches_checked": len(active_searches),
            "new_opportunities": total_new_opportunities,
            "searches_with_new_leads": searches_with_new_leads,
            "email_sent": email_sent,
            "message": f"Refreshed {len(active_searches)} searches, found {total_new_opportunities} new opportunities"
        }
        
    finally:
        db.close()


async def refresh_for_search(search_id: str) -> dict:
    """
    Refresh opportunities for a specific keyword search.
    
    Args:
        search_id: Keyword search UUID
        
    Returns:
        dict: Refresh results
    """
    db = SessionLocal()
    try:
        search = db.query(KeywordSearch).filter(KeywordSearch.id == search_id).first()
        if not search:
            return {
                "status": "error",
                "message": f"Keyword search with ID {search_id} not found"
            }
        
        if not search.zola_search_id:  # type: ignore
            return {
                "status": "error",
                "message": f"Keyword search {search_id} does not have a Rixly search ID"
            }
        
        user = db.query(User).filter(User.id == search.user_id).first()
        if not user:
            return {
                "status": "error",
                "message": f"User for search {search_id} not found"
            }
        
        logger.info(f"Refreshing opportunities for search: {search.name} (ID: {search_id})")
        
        # Fetch leads from Rixly
        leads = await OpportunityService.fetch_leads_from_rixly(
            rixly_search_id=search.zola_search_id,  # type: ignore
            limit=500,
            offset=0
        )
        
        if not leads:
            return {
                "status": "success",
                "search_id": search_id,
                "search_name": search.name,
                "leads_fetched": 0,
                "new_opportunities": 0,
                "message": "No leads found in Rixly"
            }
        
        logger.info(f"Fetched {len(leads)} leads from Rixly")
        
        # Extract source_post_ids from leads (batch check is more efficient)
        source_post_ids = []
        valid_leads = []
        
        for lead in leads:
            source_post_id = lead.get("source_id") or lead.get("source_post_id") or lead.get("id", "")
            if not source_post_id:
                logger.warning(f"Skipping lead without source_id: {lead.get('title', 'Unknown')}")
                continue
            source_post_ids.append(source_post_id)
            valid_leads.append((source_post_id, lead))
        
        # Batch check for existing opportunities (more efficient than checking one-by-one)
        # IMPORTANT: Check by user_id, not just keyword_search_id, because UniqueConstraint
        # is on (user_id, source_post_id) - same lead can't exist twice for same user
        from models.opportunity import Opportunity
        from sqlalchemy.exc import IntegrityError
        
        existing_source_ids = set()
        if source_post_ids:
            existing = db.query(Opportunity.source_post_id).filter(
                Opportunity.user_id == user.id,
                Opportunity.source_post_id.in_(source_post_ids)
            ).all()
            existing_source_ids = {row[0] for row in existing}
            logger.info(
                f"Found {len(existing_source_ids)} existing opportunities out of {len(source_post_ids)} leads"
            )
        
        # Process new leads
        new_count = 0
        for source_post_id, lead in valid_leads:
            if source_post_id in existing_source_ids:
                continue
            
            try:
                opportunity = OpportunityService.convert_zola_lead_to_opportunity(
                    zola_lead=lead,
                    user_id=user.id,
                    keyword_search_id=search.id
                )
                db.add(opportunity)
                new_count += 1
            except IntegrityError as e:
                # Handle database constraint violation (duplicate)
                db.rollback()
                logger.warning(f"Duplicate opportunity detected (constraint violation): {source_post_id}")
                continue
            except Exception as e:
                logger.error(f"Error converting lead to opportunity: {str(e)}")
                db.rollback()
                continue
        
        if new_count > 0:
            db.commit()
            logger.info(f"Created {new_count} new opportunities")
        else:
            logger.info(f"No new opportunities (all {len(leads)} leads already exist)")
        
        return {
            "status": "success",
            "search_id": search_id,
            "search_name": search.name,
            "leads_fetched": len(leads),
            "new_opportunities": new_count,
            "existing_opportunities": len(existing_source_ids),
            "message": f"Fetched {len(leads)} leads, created {new_count} new opportunities"
        }
        
    finally:
        db.close()


async def refresh_for_all_users(include_scheduled: bool = False) -> dict:
    """
    Refresh opportunities for all users with active searches.
    
    Args:
        include_scheduled: Whether to include scheduled searches
        
    Returns:
        dict: Refresh results
    """
    logger.info("Refreshing opportunities for all users...")
    result = await LeadRefreshService.refresh_leads_for_all_users(SessionLocal())
    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Manually refresh opportunities from Rixly API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # User selection (mutually exclusive)
    user_group = parser.add_mutually_exclusive_group()
    user_group.add_argument(
        "--user-email",
        type=str,
        help="User email address to refresh opportunities for"
    )
    user_group.add_argument(
        "--user-id",
        type=str,
        help="User UUID to refresh opportunities for"
    )
    user_group.add_argument(
        "--search-id",
        type=str,
        help="Keyword search UUID to refresh opportunities for"
    )
    user_group.add_argument(
        "--all-users",
        action="store_true",
        help="Refresh opportunities for all users with active searches"
    )
    
    # Options
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Don't send email notifications"
    )
    parser.add_argument(
        "--include-scheduled",
        action="store_true",
        help="Include scheduled searches (normally only one_time searches are refreshed)"
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if not any([args.user_email, args.user_id, args.search_id, args.all_users]):
        parser.error("One of --user-email, --user-id, --search-id, or --all-users must be provided")
    
    # Run the appropriate refresh function
    try:
        if args.search_id:
            result = asyncio.run(refresh_for_search(args.search_id))
        elif args.all_users:
            result = asyncio.run(refresh_for_all_users(include_scheduled=args.include_scheduled))
        else:
            result = asyncio.run(refresh_for_user(
                user_email=args.user_email,
                user_id=args.user_id,
                send_email=not args.no_email,
                include_scheduled=args.include_scheduled
            ))
        
        # Print results
        print("\n" + "=" * 60)
        print("REFRESH RESULTS")
        print("=" * 60)
        for key, value in result.items():
            if key not in ["searches_with_new_leads", "debug_info"]:  # Print these separately
                print(f"{key}: {value}")
        
        if "debug_info" in result and result["debug_info"]:
            print("\nDebug Information:")
            for key, value in result["debug_info"].items():
                print(f"  {key}: {value}")
        
        if "searches_with_new_leads" in result and result["searches_with_new_leads"]:
            print("\nSearches with new leads:")
            for search in result["searches_with_new_leads"]:
                print(f"  - {search['search_name']}: {search['new_count']} new opportunities")
        
        print("=" * 60)
        
        if result.get("status") == "error":
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        print(f"\nError: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()

