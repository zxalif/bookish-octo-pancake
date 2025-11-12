"""
Support Thread Model

Represents a support ticket/thread between a user and support team.
"""

from sqlalchemy import Column, String, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.orm import relationship
import enum

from models.base import generate_uuid, TimestampMixin
from core.database import Base


class ThreadStatus(str, enum.Enum):
    """Support thread status."""
    OPEN = "open"
    PENDING = "pending"
    CLOSED = "closed"


class SupportThread(Base, TimestampMixin):
    """
    Support thread model.
    
    Represents a support ticket/thread between a user and support team.
    """
    __tablename__ = "support_threads"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subject = Column(String, nullable=False)
    status = Column(SQLEnum(ThreadStatus), default=ThreadStatus.OPEN, nullable=False, index=True)
    
    # Relationships
    user = relationship("User", back_populates="support_threads")
    messages = relationship("SupportMessage", back_populates="thread", cascade="all, delete-orphan", order_by="SupportMessage.created_at")
    
    def to_dict(self):
        """Convert to dictionary."""
        from models.support_message import MessageSender
        return {
            "id": self.id,
            "user_id": self.user_id,
            "subject": self.subject,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_message_at": self.messages[-1].created_at.isoformat() if self.messages else None,
            "unread_count": sum(1 for msg in self.messages if msg.sender == MessageSender.SUPPORT and not msg.read),
        }


