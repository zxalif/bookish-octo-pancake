"""
Usage Metric Model

Tracks user usage for plan limits enforcement.
"""

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, Index
from sqlalchemy.orm import relationship

from core.database import Base
from models.base import generate_uuid, TimestampMixin


class UsageMetric(Base, TimestampMixin):
    """
    Usage metric model for tracking user usage.
    
    IMPORTANT: Keyword searches are CONCURRENT limits, not monthly usage.
    - Users can enable/disable/delete searches anytime
    - Limit is about how many searches are ACTIVE at once
    - Opportunities are MONTHLY limits (resets each billing period)
    
    Attributes:
        id: Unique metric identifier (UUID)
        user_id: Foreign key to User
        subscription_id: Foreign key to Subscription
        metric_type: Type of metric (keyword_searches, opportunities, api_calls)
        count: Current count for this metric
        period_start: Start of tracking period
        period_end: End of tracking period
        created_at: Metric creation timestamp
        updated_at: Last update timestamp
        
    Relationships:
        user: Associated user
        subscription: Associated subscription
    """
    
    __tablename__ = "usage_metrics"
    
    __table_args__ = (
        # One metric per user per type per period
        UniqueConstraint('user_id', 'metric_type', 'period_start', name='uq_usage_metric_user_type_period'),
        # Composite index for common queries
        Index('ix_usage_metrics_user_type_period', 'user_id', 'metric_type', 'period_start'),
        # Ensure count is non-negative
        CheckConstraint('count >= 0', name='check_usage_count_positive'),
    )
    
    # Primary Key
    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    # Foreign Keys
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    subscription_id = Column(String(36), ForeignKey("subscriptions.id"), nullable=False, index=True)
    
    # Metric Details
    metric_type = Column(String(50), nullable=False, index=True)  # keyword_searches, opportunities, api_calls
    count = Column(Integer, nullable=False, default=0)
    
    # Tracking Period
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="usage_metrics")
    subscription = relationship("Subscription", back_populates="usage_metrics")
    
    def is_over_limit(self, limit: int) -> bool:
        """Check if usage is over the limit."""
        return self.count >= limit
    
    def percentage_used(self, limit: int) -> float:
        """Get percentage of limit used."""
        if limit == 0:
            return 0.0
        return min(100.0, (self.count / limit) * 100)
    
    def __repr__(self):
        return f"<UsageMetric(id={self.id}, user_id={self.user_id}, metric_type={self.metric_type}, count={self.count})>"
    
    def to_dict(self):
        """
        Convert usage metric to dictionary.
        
        Returns:
            dict: Usage metric data
        """
        return {
            "id": self.id,
            "user_id": self.user_id,
            "subscription_id": self.subscription_id,
            "metric_type": self.metric_type,
            "count": self.count,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
