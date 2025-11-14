"""
Admin Routes

Handles admin-only operations:
- User management
- Subscription management
- Analytics and statistics
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from typing import Optional, List
from datetime import datetime, timedelta
from pydantic import BaseModel, EmailStr, Field

from core.database import get_db
from api.dependencies import get_admin_user, require_csrf_protection
from api.middleware.rate_limit import limiter
from models.user import User
from models.subscription import Subscription, SubscriptionStatus, SubscriptionPlan
from models.payment import Payment
from models.usage_metric import UsageMetric
from models.keyword_search import KeywordSearch
from models.opportunity import Opportunity
from models.support_thread import SupportThread, ThreadStatus
from models.support_message import SupportMessage, MessageSender
from services.admin_analytics_service import AdminAnalyticsService
from services.support_service import SupportService
from services.email_service import EmailService
from core.logger import get_logger
from core.sanitization import sanitize_message, sanitize_subject
from bleach import clean

router = APIRouter()
logger = get_logger(__name__)

# SECURITY: Admin endpoints have higher rate limits (100/minute vs 60/minute default)

# ===== Response Models =====

class UserListResponse(BaseModel):
    """User list response model."""
    users: List[dict]
    total: int
    page: int
    limit: int

class UserDetailResponse(BaseModel):
    """User detail response model."""
    id: str
    email: str
    full_name: str
    is_active: bool
    is_verified: bool
    is_admin: bool
    is_banned: bool
    created_at: str
    updated_at: str
    subscription_count: int
    payment_count: int
    keyword_search_count: int
    opportunity_count: int
    subscriptions: List[dict] = []

class UserUpdateAdmin(BaseModel):
    """User update request model (admin)."""
    full_name: Optional[str] = Field(None, max_length=255, description="User full name (max 255 characters)")
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    is_admin: Optional[bool] = None
    is_banned: Optional[bool] = None

class SendEmailRequest(BaseModel):
    """Send email request model."""
    to_email: EmailStr
    subject: str = Field(..., max_length=200, description="Email subject (max 200 characters)")
    message: str = Field(..., max_length=10000, description="Email message (max 10,000 characters)")
    html: Optional[bool] = True

class ReplyToThreadRequest(BaseModel):
    """Reply to thread request model."""
    message: str = Field(..., max_length=5000, description="Reply message (max 5,000 characters)")

class UpdateThreadStatusRequest(BaseModel):
    """Update thread status request model."""
    status: ThreadStatus

# ===== User Management =====

@router.get("/users", response_model=UserListResponse)
@limiter.limit("100/minute")
async def list_users(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    is_verified: Optional[bool] = Query(None),
    has_subscription: Optional[bool] = Query(None),
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    List all users with filtering and pagination.
    
    **Admin Only**: Requires admin role.
    
    **Query Parameters**:
    - skip: Number of records to skip (pagination)
    - limit: Maximum number of records to return (1-100)
    - search: Search by email or full name
    - is_active: Filter by active status
    - is_verified: Filter by verified status
    - has_subscription: Filter by subscription status
    
    **Response 200**: List of users with pagination info
    **Response 403**: Not an admin
    """
    query = db.query(User)
    
    # Apply filters
    if search:
        # SECURITY: Limit search length and sanitize to prevent abuse
        search_clean = search.strip()[:100]  # Max 100 characters
        if search_clean:
            search_filter = or_(
                User.email.ilike(f"%{search_clean}%"),
                User.full_name.ilike(f"%{search_clean}%")
            )
            query = query.filter(search_filter)
    
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    
    if is_verified is not None:
        query = query.filter(User.is_verified == is_verified)
    
    if has_subscription is not None:
        if has_subscription:
            # Users with active subscriptions
            query = query.join(Subscription).filter(
                Subscription.status == SubscriptionStatus.ACTIVE
            )
        else:
            # Users without active subscriptions
            subquery = db.query(Subscription.user_id).filter(
                Subscription.status == SubscriptionStatus.ACTIVE
            ).subquery()
            query = query.outerjoin(subquery, User.id == subquery.c.user_id).filter(
                subquery.c.user_id.is_(None)
            )
    
    # Get total count
    total = query.count()
    
    # Apply pagination
    users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    
    # Build response
    result = []
    for user in users:
        active_sub = user.get_active_subscription()
        result.append({
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "is_admin": user.is_admin,
            "is_banned": user.is_banned,
            "created_at": user.created_at.isoformat(),
            "has_active_subscription": active_sub is not None,
            "subscription_plan": active_sub.plan.value if active_sub else None
        })
    
    return {
        "users": result,
        "total": total,
        "page": (skip // limit) + 1,
        "limit": limit
    }


@router.get("/users/{user_id}", response_model=UserDetailResponse)
@limiter.limit("100/minute")
async def get_user_detail(
    request: Request,
    user_id: str,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get detailed user information.
    
    **Admin Only**: Requires admin role.
    
    **Response 200**: User details
    **Response 404**: User not found
    **Response 403**: Not an admin
    """
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Get counts
    subscription_count = len(user.subscriptions)
    payment_count = len(user.payments)
    keyword_search_count = len(user.keyword_searches)
    opportunity_count = len(user.opportunities)
    
    # Get subscriptions
    subscriptions = []
    for sub in user.subscriptions:
        subscriptions.append({
            "id": sub.id,
            "plan": sub.plan.value,
            "status": sub.status.value,
            "billing_period": sub.billing_period.value,
            "created_at": sub.created_at.isoformat(),
            "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        })
    
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "is_admin": user.is_admin,
        "is_banned": user.is_banned,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
        "subscription_count": subscription_count,
        "payment_count": payment_count,
        "keyword_search_count": keyword_search_count,
        "opportunity_count": opportunity_count,
        "subscriptions": subscriptions
    }


@router.put("/users/{user_id}")
@limiter.limit("100/minute")
async def update_user(
    request: Request,
    user_id: str,
    user_update: UserUpdateAdmin,
    admin_user: User = Depends(require_csrf_protection),
    db: Session = Depends(get_db)
):
    """
    Update user (admin only).
    
    **Admin Only**: Requires admin role.
    
    **Response 200**: Updated user
    **Response 404**: User not found
    **Response 403**: Not an admin
    """
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent admin from removing their own admin status
    if admin_user.id == user_id and user_update.is_admin is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove your own admin status"
        )
    
    # Update fields
    if user_update.full_name is not None:
        # SECURITY: Sanitize full name to prevent XSS
        from core.sanitization import sanitize_name
        user.full_name = sanitize_name(user_update.full_name)
    if user_update.is_active is not None:
        user.is_active = user_update.is_active
    if user_update.is_verified is not None:
        user.is_verified = user_update.is_verified
    if user_update.is_admin is not None:
        user.is_admin = user_update.is_admin
    if user_update.is_banned is not None:
        user.is_banned = user_update.is_banned
    
    # SECURITY: Log admin action for audit trail
    logger.info(
        f"Admin action: update_user",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "target_user_id": user_id,
            "changes": user_update.dict(exclude_unset=True),
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    db.commit()
    db.refresh(user)
    
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "is_admin": user.is_admin,
        "is_banned": user.is_banned
    }


@router.delete("/users/{user_id}")
@limiter.limit("50/minute")  # Lower limit for destructive actions
async def delete_user(
    request: Request,
    user_id: str,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Delete user (admin only).
    
    **Admin Only**: Requires admin role.
    
    **Response 200**: Success message
    **Response 404**: User not found
    **Response 403**: Not an admin
    """
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent admin from deleting themselves
    if admin_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    # SECURITY: Log admin action for audit trail
    logger.warning(
        f"Admin action: delete_user",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "target_user_id": user_id,
            "target_email": user.email,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    db.delete(user)
    db.commit()
    
    return {"message": "User deleted successfully"}


@router.post("/users/{user_id}/activate")
@limiter.limit("100/minute")
async def activate_user(
    request: Request,
    user_id: str,
    admin_user: User = Depends(require_csrf_protection),
    db: Session = Depends(get_db)
):
    """Activate a user account."""
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    user.is_active = True
    
    # SECURITY: Log admin action
    logger.info(
        f"Admin action: activate_user",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "target_user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    db.commit()
    
    return {"message": "User activated successfully"}


@router.post("/users/{user_id}/deactivate")
@limiter.limit("100/minute")
async def deactivate_user(
    request: Request,
    user_id: str,
    admin_user: User = Depends(require_csrf_protection),
    db: Session = Depends(get_db)
):
    """Deactivate a user account."""
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent admin from deactivating themselves
    if admin_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account"
        )
    
    user.is_active = False
    
    # SECURITY: Log admin action
    logger.info(
        f"Admin action: deactivate_user",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "target_user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    db.commit()
    
    return {"message": "User deactivated successfully"}


@router.post("/users/{user_id}/ban")
@limiter.limit("50/minute")
async def ban_user(
    request: Request,
    user_id: str,
    admin_user: User = Depends(require_csrf_protection),
    db: Session = Depends(get_db)
):
    """Ban a user account."""
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent admin from banning themselves
    if admin_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot ban your own account"
        )
    
    user.is_banned = True
    user.is_active = False  # Also deactivate when banning
    
    # SECURITY: Log admin action
    logger.warning(
        f"Admin action: ban_user",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "target_user_id": user_id,
            "target_email": user.email,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    db.commit()
    
    return {"message": "User banned successfully"}


@router.post("/users/{user_id}/unban")
@limiter.limit("50/minute")
async def unban_user(
    request: Request,
    user_id: str,
    admin_user: User = Depends(require_csrf_protection),
    db: Session = Depends(get_db)
):
    """Unban a user account."""
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    user.is_banned = False
    
    # SECURITY: Log admin action
    logger.info(
        f"Admin action: unban_user",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "target_user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    db.commit()
    
    return {"message": "User unbanned successfully"}


@router.post("/users/{user_id}/send-email")
@limiter.limit("20/minute")  # Lower limit for email sending
async def send_email_to_user(
    request: Request,
    user_id: str,
    email_data: SendEmailRequest,
    admin_user: User = Depends(require_csrf_protection),
    db: Session = Depends(get_db)
):
    """
    Send email to a user (admin only).
    
    **Admin Only**: Requires admin role.
    
    **Response 200**: Success message
    **Response 404**: User not found
    **Response 403**: Not an admin
    """
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Verify email matches user
    if email_data.to_email != user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email address does not match user"
        )
    
    # Send email
    try:
        # SECURITY: Sanitize subject and message to prevent XSS
        sanitized_subject = sanitize_subject(email_data.subject)
        sanitized_message = sanitize_message(email_data.message)
        
        if email_data.html:
            # SECURITY: Sanitize HTML content - allow only safe HTML tags
            # Convert newlines to <br> tags, then sanitize
            message_with_breaks = sanitized_message.replace('\n', '<br>')
            # Sanitize HTML to allow only safe tags
            sanitized_html = clean(
                message_with_breaks,
                tags=['p', 'br', 'strong', 'em', 'u', 'a', 'ul', 'ol', 'li', 'h1', 'h2', 'h3'],
                attributes={'a': ['href', 'title']},
                strip=True
            )
            
            html_body = f"""
            <html>
              <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                  <h2 style="color: #2563eb;">{sanitized_subject}</h2>
                  <div style="margin-top: 20px;">
                    {sanitized_html}
                  </div>
                  <p style="margin-top: 30px; color: #6b7280; font-size: 14px;">
                    Best regards,<br>
                    ClientHunt Team
                  </p>
                </div>
              </body>
            </html>
            """
            text_body = sanitized_message
        else:
            html_body = None
            text_body = sanitized_message
        
        email_sent = EmailService._send_email(
            email_data.to_email,
            sanitized_subject,
            html_body or sanitized_message,
            text_body
        )
        
        if not email_sent:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send email"
            )
        
        # SECURITY: Log admin action
        logger.info(
            f"Admin action: send_email_to_user",
            extra={
                "admin_user_id": admin_user.id,
                "admin_email": admin_user.email,
                "target_user_id": user_id,
                "target_email": user.email,
                "subject": email_data.subject,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
        
        return {"message": "Email sent successfully"}
        
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send email: {str(e)}"
        )


# ===== Subscription Management =====

@router.get("/subscriptions")
@limiter.limit("100/minute")
async def list_subscriptions(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None),
    plan: Optional[str] = Query(None),
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    List all subscriptions with filtering and pagination.
    
    **Admin Only**: Requires admin role.
    
    **Query Parameters**:
    - skip: Number of records to skip (pagination)
    - limit: Maximum number of records to return (1-100)
    - status: Filter by subscription status
    - plan: Filter by subscription plan
    
    **Response 200**: List of subscriptions
    **Response 403**: Not an admin
    """
    query = db.query(Subscription).join(User)
    
    # Apply filters
    if status:
        try:
            status_enum = SubscriptionStatus(status)
            query = query.filter(Subscription.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status}"
            )
    
    if plan:
        try:
            plan_enum = SubscriptionPlan(plan)
            query = query.filter(Subscription.plan == plan_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid plan: {plan}"
            )
    
    # Get total count
    total = query.count()
    
    # Apply pagination
    subscriptions = query.order_by(Subscription.created_at.desc()).offset(skip).limit(limit).all()
    
    # Build response
    result = []
    for sub in subscriptions:
        result.append({
            "id": sub.id,
            "user_id": sub.user_id,
            "user_email": sub.user.email,
            "plan": sub.plan.value,
            "status": sub.status.value,
            "billing_period": sub.billing_period.value,
            "created_at": sub.created_at.isoformat(),
            "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        })
    
    return {
        "total": total,
        "subscriptions": result,
        "page": (skip // limit) + 1,
        "limit": limit
    }


# ===== Analytics =====

@router.get("/analytics/overview")
@limiter.limit("100/minute")
async def get_analytics_overview(
    request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Get key analytics metrics."""
    return AdminAnalyticsService.get_overview_stats(db)


@router.get("/analytics/revenue")
@limiter.limit("100/minute")
async def get_revenue_analytics(
    request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Get revenue analytics."""
    return AdminAnalyticsService.get_revenue_stats(db)


@router.get("/analytics/users")
@limiter.limit("100/minute")
async def get_user_analytics(
    request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Get user analytics."""
    return AdminAnalyticsService.get_user_stats(db)


@router.get("/analytics/subscriptions")
@limiter.limit("100/minute")
async def get_subscription_analytics(
    request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Get subscription analytics."""
    return AdminAnalyticsService.get_subscription_stats(db)


@router.get("/analytics/revenue-by-plan")
@limiter.limit("100/minute")
async def get_revenue_by_plan(
    request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Get revenue breakdown by plan."""
    revenue_stats = AdminAnalyticsService.get_revenue_stats(db)
    return {
        "revenue_by_plan": revenue_stats.get("revenue_by_plan", {})
    }


@router.get("/analytics/user-growth")
@limiter.limit("100/minute")
async def get_user_growth(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Get user growth over time."""
    start_date = datetime.utcnow() - timedelta(days=days)
    user_stats = AdminAnalyticsService.get_user_stats(db, start_date=start_date)
    return {
        "user_growth": user_stats.get("user_growth", [])
    }


# ===== Support Thread Management =====

@router.get("/support/threads")
@limiter.limit("100/minute")
async def list_support_threads(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None),
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    List all support threads.
    
    **Admin Only**: Requires admin role.
    """
    query = db.query(SupportThread).join(User)
    
    # Apply filters
    if status:
        try:
            status_enum = ThreadStatus(status)
            query = query.filter(SupportThread.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status}"
            )
    
    # Get total count
    total = query.count()
    
    # Apply pagination
    threads = query.order_by(SupportThread.updated_at.desc()).offset(skip).limit(limit).all()
    
    # Build response
    result = []
    for thread in threads:
        result.append({
            "id": thread.id,
            "user_id": thread.user_id,
            "user_email": thread.user.email,
            "user_name": thread.user.full_name,
            "subject": thread.subject,
            "status": thread.status.value,
            "created_at": thread.created_at.isoformat(),
            "updated_at": thread.updated_at.isoformat(),
            "message_count": len(thread.messages),
            "unread_count": sum(1 for msg in thread.messages if msg.sender == MessageSender.USER and not msg.read),
        })
    
    return {
        "total": total,
        "threads": result,
        "page": (skip // limit) + 1,
        "limit": limit
    }


@router.get("/support/threads/{thread_id}")
@limiter.limit("100/minute")
async def get_support_thread(
    request: Request,
    thread_id: str,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Get support thread details with messages."""
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id).first()
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found"
        )
    
    # Mark admin/support messages as read
    for message in thread.messages:
        if message.sender == MessageSender.SUPPORT and not message.read:
            message.read = True
    
    db.commit()
    
    return {
        "id": thread.id,
        "user_id": thread.user_id,
        "user_email": thread.user.email,
        "user_name": thread.user.full_name,
        "subject": thread.subject,
        "status": thread.status.value,
        "created_at": thread.created_at.isoformat(),
        "updated_at": thread.updated_at.isoformat(),
        "messages": [
            {
                "id": msg.id,
                "content": msg.content,
                "sender": msg.sender.value,
                "read": msg.read,
                "created_at": msg.created_at.isoformat(),
            }
            for msg in sorted(thread.messages, key=lambda x: x.created_at)
        ]
    }


@router.post("/support/threads/{thread_id}/reply")
@limiter.limit("100/minute")
async def reply_to_thread(
    request: Request,
    thread_id: str,
    reply_data: ReplyToThreadRequest,
    admin_user: User = Depends(require_csrf_protection),
    db: Session = Depends(get_db)
):
    """Reply to a support thread."""
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id).first()
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found"
        )
    
    # SECURITY: Sanitize message content to prevent XSS
    sanitized_content = sanitize_message(reply_data.message)
    
    # Add admin/support reply
    message = SupportMessage(
        thread_id=thread.id,
        content=sanitized_content,
        sender=MessageSender.SUPPORT,
        read=True
    )
    db.add(message)
    
    # Update thread status to pending if it was closed
    if thread.status == ThreadStatus.CLOSED:
        thread.status = ThreadStatus.PENDING
    
    thread.updated_at = datetime.utcnow()
    
    # SECURITY: Log admin action
    logger.info(
        f"Admin action: reply_to_thread",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "thread_id": thread_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    db.commit()
    
    return {"message": "Reply sent successfully"}


@router.put("/support/threads/{thread_id}/status")
@limiter.limit("100/minute")
async def update_thread_status(
    request: Request,
    thread_id: str,
    status_data: UpdateThreadStatusRequest,
    admin_user: User = Depends(require_csrf_protection),
    db: Session = Depends(get_db)
):
    """Update support thread status."""
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id).first()
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found"
        )
    
    thread.status = status_data.status
    
    # SECURITY: Log admin action
    logger.info(
        f"Admin action: update_thread_status",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "thread_id": thread_id,
            "new_status": status_data.status.value,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    db.commit()
    
    return {"message": "Thread status updated successfully", "status": status_data.status.value}
