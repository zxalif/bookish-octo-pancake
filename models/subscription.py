"""
Subscription Model

Represents a user's subscription to a pricing plan.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, Enum as SQLEnum, Boolean, Index
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from core.database import Base
from models.base import generate_uuid, TimestampMixin, format_utc_datetime
from models.price import BillingPeriod


class SubscriptionStatus(enum.Enum):
    """Subscription status enumeration."""
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PAST_DUE = "past_due"
    TRIALING = "trialing"


class SubscriptionPlan(enum.Enum):
    """Subscription plan enumeration."""
    FREE = "free"  # Free tier for 3 months (validation period)
    STARTER = "starter"
    PROFESSIONAL = "professional"
    POWER = "power"


class Subscription(Base, TimestampMixin):
    """
    Subscription model for managing user subscriptions.
    
    Attributes:
        id: Unique subscription identifier (UUID)
        user_id: Foreign key to User
        plan: Subscription plan (starter, professional, power)
        status: Subscription status (active, cancelled, expired, etc.)
        paddle_subscription_id: Paddle subscription ID
        current_period_start: Start of current billing period
        current_period_end: End of current billing period
        cancel_at_period_end: Whether to cancel at period end
        created_at: Subscription creation timestamp
        updated_at: Last update timestamp
        
    Relationships:
        user: Associated user
        payments: Payments for this subscription
        usage_metrics: Usage metrics for this subscription
    """
    
    __tablename__ = "subscriptions"
    
    # Primary Key
    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    __table_args__ = (
        # Composite index for common query: user_id + status
        Index('ix_subscriptions_user_status', 'user_id', 'status'),
    )
    
    # Foreign Keys
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    price_id = Column(String(36), ForeignKey("prices.id"), nullable=True, index=True)  # Reference to Price model
    
    # Subscription Details
    plan = Column(SQLEnum(SubscriptionPlan), nullable=False, index=True)
    status = Column(SQLEnum(SubscriptionStatus), nullable=False, default=SubscriptionStatus.ACTIVE, index=True)
    
    # Billing Period (stored here for quick access, also in Price)
    billing_period = Column(SQLEnum(BillingPeriod), nullable=False, default=BillingPeriod.MONTHLY, index=True)
    
    # Payment Integration
    paddle_subscription_id = Column(String(255), nullable=True, unique=True, index=True)
    
    # Billing Period Dates
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False, nullable=False)  # Fixed: Now Boolean instead of String
    
    # Billing History & Status
    last_billing_date = Column(DateTime, nullable=True, index=True)  # When the last payment was made
    next_billing_date = Column(DateTime, nullable=True, index=True)  # When the next payment is due
    last_billing_status = Column(String(50), nullable=True)  # Status of last billing: "completed", "failed", "pending", etc.
    trial_end_date = Column(DateTime, nullable=True)  # When the trial period ends (if applicable)
    
    # Relationships
    user = relationship("User", back_populates="subscriptions")
    price = relationship("Price", back_populates="subscriptions")  # Reference to Price model
    payments = relationship("Payment", back_populates="subscription", cascade="all, delete-orphan")
    usage_metrics = relationship("UsageMetric", back_populates="subscription", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Subscription(id={self.id}, user_id={self.user_id}, plan={self.plan.value}, status={self.status.value})>"
    
    def is_active(self) -> bool:
        """Check if subscription is active."""
        return self.status == SubscriptionStatus.ACTIVE
    
    def is_trialing(self) -> bool:
        """Check if subscription is in trial."""
        return self.status == SubscriptionStatus.TRIALING
    
    def days_until_renewal(self) -> int:
        """Get days until subscription renewal."""
        if not self.current_period_end:
            return 0
        delta = self.current_period_end - datetime.utcnow()
        return max(0, delta.days)
    
    def to_dict(self):
        """
        Convert subscription to dictionary.
        
        Returns:
            dict: Subscription data
        """
        return {
            "id": self.id,
            "user_id": self.user_id,
            "price_id": self.price_id,
            "plan": self.plan.value,
            "billing_period": self.billing_period.value if self.billing_period else None,
            "status": self.status.value,
            "paddle_subscription_id": self.paddle_subscription_id,
            "current_period_start": format_utc_datetime(self.current_period_start),  # Format with UTC indicator
            "current_period_end": format_utc_datetime(self.current_period_end),  # Format with UTC indicator
            "cancel_at_period_end": self.cancel_at_period_end,  # Already boolean
            "last_billing_date": format_utc_datetime(self.last_billing_date),  # Format with UTC indicator
            "next_billing_date": format_utc_datetime(self.next_billing_date),  # Format with UTC indicator
            "last_billing_status": self.last_billing_status,
            "trial_end_date": format_utc_datetime(self.trial_end_date),  # Format with UTC indicator
            "created_at": format_utc_datetime(self.created_at),  # Format with UTC indicator
            "updated_at": format_utc_datetime(self.updated_at),  # Format with UTC indicator
        }
