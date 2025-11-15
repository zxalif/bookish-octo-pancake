"""
Base utilities for database models.

Centralized functions and mixins used across all models.
"""

import uuid
from sqlalchemy import Column, DateTime
from datetime import datetime
from typing import Optional


def generate_uuid() -> str:
    """
    Generate a UUID string.
    
    Returns:
        str: UUID string
    """
    return str(uuid.uuid4())


def format_utc_datetime(dt: Optional[datetime]) -> Optional[str]:
    """
    Format a UTC datetime to ISO format with 'Z' suffix.
    
    Since all datetimes in the database are stored as UTC (using datetime.utcnow),
    we append 'Z' to indicate UTC timezone so JavaScript can properly convert to local time.
    
    Args:
        dt: Datetime object (assumed to be UTC)
        
    Returns:
        ISO format string with 'Z' suffix (e.g., "2025-11-15T09:33:00Z") or None
    """
    if dt is None:
        return None
    # Append 'Z' to indicate UTC timezone
    iso_str = dt.isoformat()
    # Only append 'Z' if it doesn't already have timezone info
    if not iso_str.endswith('Z') and '+' not in iso_str and iso_str.count('-') <= 2:
        return iso_str + 'Z'
    return iso_str


class TimestampMixin:
    """Mixin for created_at and updated_at timestamps."""
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SoftDeleteMixin:
    """Mixin for soft delete functionality."""
    
    deleted_at = Column(DateTime, nullable=True, index=True)
    
    def soft_delete(self):
        """Soft delete the record."""
        self.deleted_at = datetime.utcnow()
    
    def restore(self):
        """Restore a soft-deleted record."""
        self.deleted_at = None
    
    def is_deleted(self) -> bool:
        """Check if record is soft-deleted."""
        return self.deleted_at is not None

