"""
Opportunity Model

Represents a freelance opportunity (renamed from "lead" for freelancer focus).
"""

from sqlalchemy import Column, String, Text, Float, ForeignKey, JSON, Enum as SQLEnum, UniqueConstraint, CheckConstraint, Index
from sqlalchemy.orm import relationship
import enum

from core.database import Base
from models.base import generate_uuid, TimestampMixin


class OpportunityStatus(enum.Enum):
    """Opportunity status enumeration."""
    NEW = "new"
    VIEWED = "viewed"
    CONTACTED = "contacted"
    APPLIED = "applied"
    REJECTED = "rejected"
    WON = "won"
    LOST = "lost"


class Opportunity(Base, TimestampMixin):
    """
    Opportunity model for managing freelance opportunities.
    
    IMPORTANT: User-scoped opportunities (multi-tenancy).
    - Same Reddit post can appear for multiple users
    - Each user has their own opportunity record
    - Users can only see their own opportunities
    
    Attributes:
        id: Unique opportunity identifier (UUID)
        user_id: Foreign key to User (multi-tenancy)
        keyword_search_id: Foreign key to KeywordSearch
        source_post_id: Original post ID from source (for deduplication)
        source: Source platform (reddit, craigslist, linkedin, twitter)
        source_type: Type of source (post, comment, job_listing, tweet)
        title: Opportunity title
        content: Opportunity content/description
        author: Author username
        url: URL to original post
        matched_keywords: Keywords that matched
        detected_pattern: Pattern that was detected
        opportunity_type: Type of opportunity (project, job, gig, etc.)
        opportunity_subtype: Subtype (web_dev, design, writing, etc.)
        relevance_score: AI relevance score (0-1)
        urgency_score: AI urgency score (0-1)
        total_score: Combined score
        extracted_info: Extracted information (budget, timeline, etc.)
        status: Opportunity status (new, viewed, contacted, etc.)
        notes: User notes
        created_at: Opportunity creation timestamp
        updated_at: Last update timestamp
        
    Relationships:
        user: Associated user
        keyword_search: Associated keyword search
    """
    
    __tablename__ = "opportunities"
    
    __table_args__ = (
        # Prevent duplicate opportunities per user (deduplication)
        UniqueConstraint('user_id', 'source_post_id', name='uq_opportunity_user_source'),
        # Composite indexes for common queries
        Index('ix_opportunities_user_status', 'user_id', 'status'),
        Index('ix_opportunities_user_created', 'user_id', 'created_at'),
        Index('ix_opportunities_user_source', 'user_id', 'source'),
        # Check constraints for score validation
        CheckConstraint('relevance_score >= 0 AND relevance_score <= 1', name='check_relevance_score_range'),
        CheckConstraint('urgency_score >= 0 AND urgency_score <= 1', name='check_urgency_score_range'),
        CheckConstraint('total_score >= 0 AND total_score <= 1', name='check_total_score_range'),
    )
    
    # Primary Key
    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    # Foreign Keys (Multi-tenancy)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    keyword_search_id = Column(String(36), ForeignKey("keyword_searches.id"), nullable=False, index=True)
    
    # Source Information
    source_post_id = Column(String(255), nullable=False, index=True)  # For deduplication per user
    source = Column(String(50), nullable=False, index=True)  # reddit, craigslist, linkedin, twitter
    source_type = Column(String(50), nullable=False)  # post, comment, job_listing, tweet
    
    # Content
    title = Column(String(500), nullable=True)
    content = Column(Text, nullable=False)
    author = Column(String(255), nullable=False)
    url = Column(Text, nullable=False)
    
    # Matching Information
    matched_keywords = Column(JSON, nullable=False)  # List of matched keywords
    detected_pattern = Column(String(255), nullable=True)
    
    # Classification (AI-powered)
    opportunity_type = Column(String(100), nullable=True)  # project, job, gig, etc.
    opportunity_subtype = Column(String(100), nullable=True)  # web_dev, design, writing, etc.
    
    # Scoring (AI-powered)
    relevance_score = Column(Float, nullable=False, default=0.0)
    urgency_score = Column(Float, nullable=False, default=0.0)
    total_score = Column(Float, nullable=False, default=0.0, index=True)
    
    # Extracted Information (AI-powered)
    extracted_info = Column(JSON, nullable=True)  # budget, timeline, requirements, etc.
    
    # User Management
    status = Column(SQLEnum(OpportunityStatus), nullable=False, default=OpportunityStatus.NEW, index=True)
    notes = Column(Text, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="opportunities")
    keyword_search = relationship("KeywordSearch", back_populates="opportunities")
    
    def validate_scores(self) -> bool:
        """Validate that scores are in correct range."""
        return (
            0 <= self.relevance_score <= 1 and
            0 <= self.urgency_score <= 1 and
            0 <= self.total_score <= 1
        )
    
    def recalculate_total_score(self):
        """Recalculate total score from relevance and urgency (weighted average)."""
        self.total_score = (self.relevance_score * 0.7) + (self.urgency_score * 0.3)
    
    def __repr__(self):
        return f"<Opportunity(id={self.id}, user_id={self.user_id}, source={self.source}, status={self.status.value})>"
    
    def to_dict(self):
        """
        Convert opportunity to dictionary.
        
        Returns:
            dict: Opportunity data
        """
        return {
            "id": self.id,
            "user_id": self.user_id,
            "keyword_search_id": self.keyword_search_id,
            "source_post_id": self.source_post_id,
            "source": self.source,
            "source_type": self.source_type,
            "title": self.title,
            "content": self.content,
            "author": self.author,
            "url": self.url,
            "matched_keywords": self.matched_keywords,
            "detected_pattern": self.detected_pattern,
            "opportunity_type": self.opportunity_type,
            "opportunity_subtype": self.opportunity_subtype,
            "relevance_score": self.relevance_score,
            "urgency_score": self.urgency_score,
            "total_score": self.total_score,
            "extracted_info": self.extracted_info,
            "status": self.status.value,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
