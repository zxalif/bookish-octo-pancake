"""
Support Message Model

Represents a message within a support thread.
"""

from sqlalchemy import Column, String, ForeignKey, Boolean, Enum as SQLEnum
from sqlalchemy.orm import relationship
import enum

from models.base import generate_uuid, TimestampMixin, format_utc_datetime
from core.database import Base


class MessageSender(str, enum.Enum):
    """Message sender type."""
    USER = "user"
    SUPPORT = "support"


class SupportMessage(Base, TimestampMixin):
    """
    Support message model.
    
    Represents a message within a support thread.
    """
    __tablename__ = "support_messages"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    thread_id = Column(String, ForeignKey("support_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(String, nullable=False)
    sender = Column(SQLEnum(MessageSender), nullable=False, index=True)
    read = Column(Boolean, default=False, nullable=False, index=True)
    
    # Relationships
    thread = relationship("SupportThread", back_populates="messages")
    
    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "content": self.content,
            "sender": self.sender.value,
            "read": self.read,
            "created_at": format_utc_datetime(self.created_at),  # Format with UTC indicator
        }

