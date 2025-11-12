"""
Payment Model

Represents a payment transaction.
"""

from sqlalchemy import Column, String, Integer, ForeignKey, Enum as SQLEnum, CheckConstraint, Index
from sqlalchemy.orm import relationship
import enum

from core.database import Base
from models.base import generate_uuid, TimestampMixin


class PaymentStatus(enum.Enum):
    """Payment status enumeration."""
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class Payment(Base, TimestampMixin):
    """
    Payment model for tracking payment transactions.
    
    Attributes:
        id: Unique payment identifier (UUID)
        user_id: Foreign key to User
        subscription_id: Foreign key to Subscription
        amount: Payment amount in cents
        currency: Currency code (e.g., USD)
        status: Payment status (pending, completed, failed, refunded)
        paddle_transaction_id: Paddle transaction ID
        paddle_invoice_id: Paddle invoice ID
        payment_method: Payment method used
        created_at: Payment creation timestamp
        updated_at: Last update timestamp
        
    Relationships:
        user: Associated user
        subscription: Associated subscription
    """
    
    __tablename__ = "payments"
    
    __table_args__ = (
        # Ensure amount is positive
        CheckConstraint('amount > 0', name='check_payment_amount_positive'),
        # Composite index for common queries
        Index('ix_payments_user_status', 'user_id', 'status'),
        Index('ix_payments_user_created', 'user_id', 'created_at'),
    )
    
    # Primary Key
    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    # Foreign Keys
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    subscription_id = Column(String(36), ForeignKey("subscriptions.id"), nullable=True, index=True)
    
    # Payment Details
    amount = Column(Integer, nullable=False)  # Amount in cents
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(SQLEnum(PaymentStatus), nullable=False, default=PaymentStatus.PENDING)
    
    # Payment Integration
    paddle_transaction_id = Column(String(255), nullable=True, unique=True, index=True)
    paddle_invoice_id = Column(String(255), nullable=True)
    payment_method = Column(String(50), nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="payments")
    subscription = relationship("Subscription", back_populates="payments")
    
    def __repr__(self):
        return f"<Payment(id={self.id}, user_id={self.user_id}, amount={self.amount}, status={self.status.value})>"
    
    def to_dict(self):
        """
        Convert payment to dictionary.
        
        Returns:
            dict: Payment data
        """
        return {
            "id": self.id,
            "user_id": self.user_id,
            "subscription_id": self.subscription_id,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status.value,
            "paddle_transaction_id": self.paddle_transaction_id,
            "paddle_invoice_id": self.paddle_invoice_id,
            "payment_method": self.payment_method,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
