"""
User Audit Log Model

Tracks user account changes with IP addresses for security and compliance.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime

from core.database import Base
from models.base import generate_uuid, TimestampMixin


class UserAuditLog(Base, TimestampMixin):
    """
    User audit log for tracking account changes with IP addresses.
    
    Tracks:
    - Registration
    - Profile updates
    - Email changes
    - Password changes
    - Consent changes
    - Account status changes
    
    Attributes:
        id: Unique log entry identifier (UUID)
        user_id: Foreign key to User
        action: Type of action (register, update_profile, change_email, change_password, update_consent, etc.)
        ip_address: IP address from which the action was performed
        user_agent: Browser/user agent string
        details: JSON or text details about the change
        created_at: Timestamp of the action
    """
    
    __tablename__ = "user_audit_logs"
    
    # Primary Key
    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    # Foreign Key
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    
    # Action Details
    action = Column(String(50), nullable=False, index=True)  # register, update_profile, change_email, etc.
    ip_address = Column(String(45), nullable=True)  # IPv6 max length is 45 chars
    user_agent = Column(String(500), nullable=True)  # Browser/user agent
    
    # Change Details
    details = Column(Text, nullable=True)  # JSON string or text description of what changed
    
    # Relationships
    user = relationship("User", back_populates="audit_logs")
    
    # Indexes
    __table_args__ = (
        Index('ix_user_audit_logs_user_action', 'user_id', 'action'),
        Index('ix_user_audit_logs_created_at', 'created_at'),
    )
    
    def __repr__(self):
        return f"<UserAuditLog(id={self.id}, user_id={self.user_id}, action={self.action})>"
    
    def to_dict(self):
        """Convert audit log to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "action": self.action,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "details": self.details,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

