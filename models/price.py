"""
Price Model

Represents a Paddle price for a subscription plan.
Prices are stored in the database for dynamic management.
"""

from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, Enum as SQLEnum, CheckConstraint, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from core.database import Base
from models.base import generate_uuid, TimestampMixin


class BillingPeriod(enum.Enum):
    """Billing period enumeration."""
    MONTHLY = "monthly"
    YEARLY = "yearly"


class Price(Base, TimestampMixin):
    """
    Price model for managing Paddle prices.
    
    Stores Paddle price IDs in the database for dynamic management.
    Each price is linked to a plan and billing period.
    
    Attributes:
        id: Unique price identifier (UUID)
        plan: Subscription plan (starter, professional, power)
        billing_period: Billing period (monthly, yearly)
        paddle_price_id: Paddle price ID (from Paddle API)
        paddle_product_id: Paddle product ID (for reference)
        amount: Price amount in cents (e.g., 1900 for $19.00)
        currency: Currency code (default: USD)
        is_active: Whether this price is currently active
        created_at: Price creation timestamp
        updated_at: Last update timestamp
        
    Relationships:
        subscriptions: Subscriptions using this price
    """
    
    __tablename__ = "prices"
    
    __table_args__ = (
        # Ensure Paddle price ID is unique
        UniqueConstraint('paddle_price_id', name='uq_price_paddle_id'),
        # Ensure amount is positive
        CheckConstraint('amount > 0', name='check_price_amount_positive'),
        # Partial unique index: only one active price per plan/billing_period
        # This is enforced at the application level, but we can add a partial unique index
        # Note: SQLAlchemy doesn't support partial unique indexes directly in __table_args__
        # We'll handle this in the PriceService.create_or_update_price method
    )
    
    # Primary Key
    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    # Price Details
    plan = Column(String(50), nullable=False, index=True)  # starter, professional, power
    billing_period = Column(SQLEnum(BillingPeriod), nullable=False, index=True)
    
    # Paddle Integration
    paddle_price_id = Column(String(255), nullable=False, unique=True, index=True)
    paddle_product_id = Column(String(255), nullable=True)
    
    # Pricing
    # IMPORTANT: This is the BASE PRICE (excluding VAT/tax)
    # Paddle handles all VAT/tax calculations automatically as Merchant of Record
    # VAT is added to customer's total, not deducted from this amount
    amount = Column(Integer, nullable=False)  # Base price in cents (excluding VAT)
    currency = Column(String(3), nullable=False, default="USD")
    
    # Status
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    # Relationships
    subscriptions = relationship("Subscription", back_populates="price")
    
    def __repr__(self):
        return f"<Price(id={self.id}, plan={self.plan}, billing_period={self.billing_period.value}, amount={self.amount})>"
    
    def to_dict(self):
        """
        Convert price to dictionary.
        
        Returns:
            dict: Price data
        """
        return {
            "id": self.id,
            "plan": self.plan,
            "billing_period": self.billing_period.value if self.billing_period else None,
            "paddle_price_id": self.paddle_price_id,
            "paddle_product_id": self.paddle_product_id,
            "amount": self.amount,
            "currency": self.currency,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
    
    def get_formatted_amount(self) -> str:
        """Get formatted price amount (e.g., $19.00)."""
        return f"${self.amount / 100:.2f}"

