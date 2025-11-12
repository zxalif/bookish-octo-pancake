"""
Base utilities for database models.

Centralized functions and mixins used across all models.
"""

import uuid
from sqlalchemy import Column, DateTime
from datetime import datetime


def generate_uuid() -> str:
    """
    Generate a UUID string.
    
    Returns:
        str: UUID string
    """
    return str(uuid.uuid4())


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

