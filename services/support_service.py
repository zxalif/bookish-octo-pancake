"""
Support Service

Handles support thread and message operations.
"""

from typing import List, Optional
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc

from models.support_thread import SupportThread, ThreadStatus
from models.support_message import SupportMessage, MessageSender
from services.email_service import EmailService


class SupportService:
    """Service for managing support threads and messages."""
    
    @staticmethod
    def get_user_threads(user_id: str, db: Session) -> List[SupportThread]:
        """
        Get all support threads for a user.
        
        Args:
            user_id: User ID
            db: Database session
            
        Returns:
            List of support threads
        """
        return db.query(SupportThread).filter(
            SupportThread.user_id == user_id
        ).order_by(desc(SupportThread.updated_at)).all()
    
    @staticmethod
    def get_thread(thread_id: str, user_id: str, db: Session) -> Optional[SupportThread]:
        """
        Get a specific support thread with all messages.
        
        Args:
            thread_id: Thread ID
            user_id: User ID (for authorization)
            db: Database session
            
        Returns:
            Support thread or None if not found
        """
        # Eagerly load messages to ensure all messages are loaded
        thread = db.query(SupportThread).options(
            joinedload(SupportThread.messages)
        ).filter(
            SupportThread.id == thread_id,
            SupportThread.user_id == user_id
        ).first()
        
        if thread:
            # Mark support messages as read when user views thread
            for message in thread.messages:
                if message.sender == MessageSender.SUPPORT and not message.read:
                    message.read = True
            db.commit()
        
        return thread
    
    @staticmethod
    def create_thread(
        user_id: str,
        subject: str,
        message: str,
        db: Session
    ) -> SupportThread:
        """
        Create a new support thread with initial message.
        
        Args:
            user_id: User ID
            subject: Thread subject
            message: Initial message content
            db: Database session
            
        Returns:
            Created support thread
        """
        # Create thread
        thread = SupportThread(
            user_id=user_id,
            subject=subject,
            status=ThreadStatus.OPEN
        )
        db.add(thread)
        db.flush()  # Get thread ID
        
        # Create initial message
        initial_message = SupportMessage(
            thread_id=thread.id,
            content=message,
            sender=MessageSender.USER,
            read=True  # User's own message is read
        )
        db.add(initial_message)
        db.commit()
        db.refresh(thread)
        
        # Send email notification to user
        try:
            from models.user import User
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                EmailService.send_support_thread_created_email(
                    user.email,
                    user.full_name,
                    thread.subject,
                    thread.id
                )
        except Exception as e:
            # Log error but don't fail the request
            from core.logger import get_logger
            logger = get_logger(__name__)
            logger.error(f"Failed to send support notification email: {str(e)}", exc_info=True)
        
        return thread
    
    @staticmethod
    def add_message(
        thread_id: str,
        user_id: str,
        content: str,
        db: Session
    ) -> SupportMessage:
        """
        Add a message to a support thread.
        
        Args:
            thread_id: Thread ID
            user_id: User ID (for authorization)
            content: Message content
            db: Database session
            
        Returns:
            Created support message
        """
        # Verify thread exists and belongs to user
        thread = db.query(SupportThread).filter(
            SupportThread.id == thread_id,
            SupportThread.user_id == user_id
        ).first()
        
        if not thread:
            raise ValueError("Thread not found")
        
        # Security: Prevent messages to closed threads
        if thread.status == ThreadStatus.CLOSED:
            raise ValueError("Cannot send messages to a closed thread. Please create a new support request.")
        
        # Create message
        message = SupportMessage(
            thread_id=thread_id,
            content=content,
            sender=MessageSender.USER,
            read=True  # User's own message is read
        )
        db.add(message)
        
        db.commit()
        db.refresh(message)
        
        return message
    
    @staticmethod
    def get_unread_notification_count(user_id: str, db: Session) -> int:
        """
        Get count of unread support messages for a user.
        
        Args:
            user_id: User ID
            db: Database session
            
        Returns:
            Count of unread messages
        """
        count = db.query(SupportMessage).join(SupportThread).filter(
            SupportThread.user_id == user_id,
            SupportMessage.sender == MessageSender.SUPPORT,
            SupportMessage.read == False
        ).count()
        
        return count

