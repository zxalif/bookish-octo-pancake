"""
User Model

Represents a user account in the ClientHunt platform.
"""

from sqlalchemy import Column, String, Boolean, Index
from sqlalchemy.orm import relationship
from typing import Optional

from core.database import Base
from models.base import generate_uuid, TimestampMixin


class User(Base, TimestampMixin):
    """
    User model for authentication and profile management.
    
    Attributes:
        id: Unique user identifier (UUID)
        email: User's email address (unique)
        password_hash: Hashed password
        full_name: User's full name
        is_active: Whether the account is active
        is_verified: Whether email is verified
        paddle_customer_id: Paddle customer ID for payments
        created_at: Account creation timestamp
        updated_at: Last update timestamp
        
    Relationships:
        subscriptions: User's subscription history
        payments: User's payment history
        usage_metrics: User's usage tracking
        keyword_searches: User's keyword searches
        opportunities: User's opportunities
    """
    
    __tablename__ = "users"
    
    # Primary Key
    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    # Authentication
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    
    # Profile
    full_name = Column(String(255), nullable=False)
    
    # Account Status
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    
    # Payment Integration
    paddle_customer_id = Column(String(255), nullable=True, index=True)
    
    # Relationships
    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    usage_metrics = relationship("UsageMetric", back_populates="user", cascade="all, delete-orphan")
    keyword_searches = relationship("KeywordSearch", back_populates="user", cascade="all, delete-orphan")
    opportunities = relationship("Opportunity", back_populates="user", cascade="all, delete-orphan")
    support_threads = relationship("SupportThread", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"
    
    def has_active_subscription(self) -> bool:
        """Check if user has active subscription."""
        return any(sub.is_active() for sub in self.subscriptions)
    
    def get_active_subscription(self) -> Optional['Subscription']:
        """Get user's active subscription."""
        for sub in self.subscriptions:
            if sub.is_active():
                return sub
        return None
    
    def to_dict(self):
        """
        Convert user to dictionary (excluding sensitive data).
        
        Returns:
            dict: User data without password
        """
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
