"""
Keyword Search Model

Represents a user's keyword search configuration (freelancer-focused).
"""

from sqlalchemy import Column, String, Text, Boolean, ForeignKey, JSON, Index, DateTime
from sqlalchemy.orm import relationship

from core.database import Base
from models.base import generate_uuid, TimestampMixin, SoftDeleteMixin


class KeywordSearch(Base, TimestampMixin, SoftDeleteMixin):
    """
    Keyword search model for managing user's search configurations.
    
    IMPORTANT: This is a CONCURRENT limit, not monthly usage.
    - Users can enable/disable/delete searches anytime
    - Users can change their searches next month
    - Limit is about how many searches are ACTIVE at once
    
    Attributes:
        id: Unique search identifier (UUID)
        user_id: Foreign key to User (multi-tenancy)
        name: Search name (e.g., "React Developers")
        keywords: List of keywords to search for
        patterns: List of patterns to match (e.g., "looking for", "need", "hiring")
        subreddits: List of subreddits to search (freelancer-focused)
        platforms: List of platforms (reddit, craigslist, linkedin, twitter)
        enabled: Whether this search is active
        last_run_at: Last time this search was executed
        created_at: Search creation timestamp
        updated_at: Last update timestamp
        
    Relationships:
        user: Associated user
        opportunities: Opportunities found by this search
    """
    
    __tablename__ = "keyword_searches"
    
    __table_args__ = (
        # Composite index for common query: user_id + enabled
        Index('ix_keyword_searches_user_enabled', 'user_id', 'enabled'),
        # Index for soft delete queries
        Index('ix_keyword_searches_deleted_at', 'deleted_at'),
    )
    
    # Primary Key
    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    # Foreign Keys (Multi-tenancy)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    
    # Search Configuration
    name = Column(String(255), nullable=False)
    keywords = Column(JSON, nullable=False)  # List of keywords
    patterns = Column(JSON, nullable=True)  # List of patterns
    subreddits = Column(JSON, nullable=True)  # List of subreddits (freelancer-focused)
    platforms = Column(JSON, nullable=False, default=["reddit"])  # List of platforms
    
    # Status
    enabled = Column(Boolean, default=True, nullable=False, index=True)
    last_run_at = Column(DateTime, nullable=True)
    
    # Scheduling Configuration
    scraping_mode = Column(String(20), default="one_time", nullable=False)  # "one_time" or "scheduled"
    scraping_interval = Column(String(10), nullable=True)  # "30m", "1h", "6h", "24h" (only for scheduled mode)
    
    # Rixly Integration
    # Note: Column name kept as zola_search_id for database compatibility, but now stores Rixly search ID
    zola_search_id = Column(String(100), nullable=True, index=True)  # Rixly search ID (for reuse)
    
    # Relationships
    user = relationship("User", back_populates="keyword_searches")
    opportunities = relationship("Opportunity", back_populates="keyword_search", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<KeywordSearch(id={self.id}, user_id={self.user_id}, name={self.name}, enabled={self.enabled})>"
    
    def to_dict(self):
        """
        Convert keyword search to dictionary.
        
        Returns:
            dict: Keyword search data
        """
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "keywords": self.keywords,
            "patterns": self.patterns,
            "subreddits": self.subreddits,
            "platforms": self.platforms,
            "enabled": self.enabled,
            "scraping_mode": self.scraping_mode,
            "scraping_interval": self.scraping_interval,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "zola_search_id": self.zola_search_id,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
