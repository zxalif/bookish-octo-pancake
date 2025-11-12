"""
Cleanup Service

Handles cleanup of soft-deleted records and expired data.
"""

from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from models.keyword_search import KeywordSearch
from core.logger import get_logger

logger = get_logger(__name__)


class CleanupService:
    """Service for cleaning up expired and soft-deleted records."""
    
    @staticmethod
    def cleanup_old_soft_deleted_searches(db: Session, days_old: int = 30) -> int:
        """
        Permanently delete keyword searches that were soft-deleted more than X days ago.
        
        This is called monthly to actually remove old soft-deleted searches.
        Soft-deleted searches count toward limit until they're permanently deleted.
        
        Args:
            db: Database session
            days_old: Delete searches soft-deleted more than this many days ago (default: 30)
            
        Returns:
            int: Number of searches permanently deleted
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days_old)
        
        # Find searches soft-deleted before cutoff date
        old_deleted_searches = db.query(KeywordSearch).filter(
            KeywordSearch.deleted_at.isnot(None),  # type: ignore
            KeywordSearch.deleted_at < cutoff_date  # type: ignore
        ).all()
        
        count = len(old_deleted_searches)
        
        if count > 0:
            # Permanently delete them
            for search in old_deleted_searches:
                db.delete(search)
            
            db.commit()
            logger.info(f"Permanently deleted {count} old soft-deleted keyword searches (deleted before {cutoff_date})")
        else:
            logger.debug("No old soft-deleted searches to clean up")
        
        return count
    
    @staticmethod
    def cleanup_current_month_soft_deleted_searches(db: Session) -> int:
        """
        Permanently delete soft-deleted searches from previous month.
        
        This is called on the 1st of each month to free up slots.
        Soft-deleted searches from previous month no longer count toward limit.
        
        Args:
            db: Database session
            
        Returns:
            int: Number of searches permanently deleted
        """
        # Get previous month
        now = datetime.utcnow()
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_month_start = (current_month_start - timedelta(days=1)).replace(day=1)
        
        # Find searches soft-deleted in previous month
        previous_month_deleted = db.query(KeywordSearch).filter(
            KeywordSearch.deleted_at.isnot(None),  # type: ignore
            KeywordSearch.deleted_at >= previous_month_start,  # type: ignore
            KeywordSearch.deleted_at < current_month_start  # type: ignore
        ).all()
        
        count = len(previous_month_deleted)
        
        if count > 0:
            # Permanently delete them
            for search in previous_month_deleted:
                db.delete(search)
            
            db.commit()
            logger.info(
                f"Permanently deleted {count} soft-deleted keyword searches from previous month "
                f"({previous_month_start} to {current_month_start}). Slots are now freed."
            )
        else:
            logger.debug("No previous month soft-deleted searches to clean up")
        
        return count

