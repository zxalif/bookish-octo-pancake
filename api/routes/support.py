"""
Support Routes

Handles support thread and message operations.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from core.logger import get_logger
from api.dependencies import get_current_user
from api.middleware.rate_limit import limiter
from models.user import User
from models.support_thread import SupportThread
from models.support_message import SupportMessage
from services.support_service import SupportService

logger = get_logger(__name__)

router = APIRouter()


# Request/Response Models
class CreateThreadRequest(BaseModel):
    """Request model for creating a support thread."""
    subject: str
    message: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "subject": "Need help with keyword searches",
                "message": "I'm having trouble setting up my keyword search..."
            }
        }


class CreateMessageRequest(BaseModel):
    """Request model for adding a message to a thread."""
    content: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "content": "I've tried the steps you suggested..."
            }
        }


class ThreadResponse(BaseModel):
    """Response model for a support thread."""
    id: str
    subject: str
    status: str
    created_at: str
    updated_at: str
    last_message_at: str | None = None
    unread_count: int = 0


class MessageResponse(BaseModel):
    """Response model for a support message."""
    id: str
    thread_id: str
    content: str
    sender: str
    read: bool
    created_at: str


class ThreadWithMessagesResponse(BaseModel):
    """Response model for a thread with messages."""
    thread: ThreadResponse
    messages: list[MessageResponse]


@router.get("/threads", response_model=list[ThreadResponse])
async def get_support_threads(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all support threads for the current user.
    
    Returns:
        List of support threads
    """
    try:
        threads = SupportService.get_user_threads(current_user.id, db)
        return [thread.to_dict() for thread in threads]
    except Exception as e:
        # Log the actual error for debugging
        logger.error(f"Error fetching support threads: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load support threads: {str(e)}"
        )


@router.get("/threads/{thread_id}", response_model=ThreadWithMessagesResponse)
async def get_support_thread(
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get a specific support thread with messages.
    
    Args:
        thread_id: Thread ID
        
    Returns:
        Thread with messages
    """
    thread = SupportService.get_thread(thread_id, current_user.id, db)
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Support thread not found"
        )
    
    return {
        "thread": thread.to_dict(),
        "messages": [message.to_dict() for message in thread.messages]
    }


@router.post("/threads", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def create_support_thread(
    request: Request,
    thread_data: CreateThreadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new support thread.
    
    **SECURITY**: Rate limited to 5 requests per minute per IP to prevent abuse.
    User input is sanitized to prevent XSS attacks.
    
    Args:
        request: FastAPI request object (for rate limiting)
        thread_data: Thread creation request
        
    Returns:
        Created support thread
        
    Response 429: Rate limit exceeded
    """
    if not thread_data.subject.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subject is required"
        )
    
    if not thread_data.message.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message is required"
        )
    
    # Input sanitization is handled in SupportService.create_thread
    thread = SupportService.create_thread(
        current_user.id,
        thread_data.subject.strip(),
        thread_data.message.strip(),
        db
    )
    
    return thread.to_dict()


@router.post("/threads/{thread_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def add_message_to_thread(
    request: Request,
    thread_id: str,
    message_data: CreateMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Add a message to a support thread.
    
    **SECURITY**: Rate limited to 10 requests per minute per IP to prevent abuse.
    User input is sanitized to prevent XSS attacks.
    
    Args:
        request: FastAPI request object (for rate limiting)
        thread_id: Thread ID
        message_data: Message creation request
        
    Returns:
        Created message
        
    Response 429: Rate limit exceeded
    """
    if not message_data.content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message content is required"
        )
    
    try:
        # Input sanitization is handled in SupportService.add_message
        message = SupportService.add_message(
            thread_id,
            current_user.id,
            message_data.content.strip(),
            db
        )
        return message.to_dict()
    except ValueError as e:
        error_message = str(e)
        # Check if it's a closed thread error (403) or not found (404)
        if "closed" in error_message.lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error_message
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_message
        )


@router.get("/notifications/unread-count")
async def get_unread_notification_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get count of unread support messages.
    
    Returns:
        Count of unread messages
    """
    count = SupportService.get_unread_notification_count(current_user.id, db)
    return {"count": count}

