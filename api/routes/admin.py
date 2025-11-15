"""
Admin Routes

Handles admin-only operations:
- User management
- Subscription management
- Analytics and statistics
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, text
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, EmailStr, Field
import httpx
import asyncio
import smtplib
import time

from core.database import get_db, engine
from api.dependencies import get_admin_user, require_csrf_protection
from api.middleware.rate_limit import limiter
from models.user import User
from models.subscription import Subscription, SubscriptionStatus, SubscriptionPlan
from models.base import format_utc_datetime
from models.payment import Payment
from models.usage_metric import UsageMetric
from models.keyword_search import KeywordSearch
from models.opportunity import Opportunity
from models.support_thread import SupportThread, ThreadStatus
from models.support_message import SupportMessage, MessageSender
from models.user_audit_log import UserAuditLog
from models.page_visit import PageVisit
from services.admin_analytics_service import AdminAnalyticsService
from services.support_service import SupportService
from services.email_service import EmailService
from services.auth_service import AuthService
from core.logger import get_logger
from core.sanitization import sanitize_message, sanitize_subject
from core.redis_client import is_redis_available, get_redis_client
from core.config import get_settings
from bleach import clean

settings = get_settings()

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

class AuditLogListResponse(BaseModel):
    """Audit log list response model."""
    logs: List[dict]
    total: int
    page: int
    limit: int

class PageVisitListResponse(BaseModel):
    """Page visit list response model."""
    visits: List[dict]
    total: int
    page: int
    limit: int

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
        # Get keyword and opportunity counts
        keyword_count = len([ks for ks in user.keyword_searches if not ks.deleted_at])
        opportunity_count = len(user.opportunities)
        result.append({
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "is_admin": user.is_admin,
            "is_banned": user.is_banned,
            "created_at": format_utc_datetime(user.created_at),
            "has_active_subscription": active_sub is not None,
            "subscription_plan": active_sub.plan.value if active_sub else None,
            "keyword_count": keyword_count,
            "opportunity_count": opportunity_count
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
            "created_at": format_utc_datetime(sub.created_at),
            "current_period_start": format_utc_datetime(sub.current_period_start),
            "current_period_end": format_utc_datetime(sub.current_period_end),
        })
    
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "is_admin": user.is_admin,
        "is_banned": user.is_banned,
        "created_at": format_utc_datetime(user.created_at),
        "updated_at": format_utc_datetime(user.updated_at),
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


@router.post("/users/{user_id}/send-verification-email")
@limiter.limit("20/minute")  # Lower limit for email sending
async def send_verification_email_to_user(
    request: Request,
    user_id: str,
    admin_user: User = Depends(require_csrf_protection),
    db: Session = Depends(get_db)
):
    """
    Send verification email to a user (admin only).
    
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
    
    try:
        # Generate verification token
        verification_token = AuthService.generate_email_verification_token(user.id)
        
        # Send verification email
        email_sent = await EmailService.send_verification_email(
            email=user.email,
            user_id=user.id,
            token=verification_token
        )
        
        if not email_sent:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send verification email"
            )
        
        # SECURITY: Log admin action
        logger.info(
            f"Admin action: send_verification_email",
            extra={
                "admin_user_id": admin_user.id,
                "admin_email": admin_user.email,
                "target_user_id": user_id,
                "target_email": user.email,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
        
        # Create audit log entry
        audit_log = UserAuditLog(
            user_id=user.id,
            action="admin_send_verification",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", ""),
            details=f"Verification email sent by admin: {admin_user.email}"
        )
        db.add(audit_log)
        db.commit()
        
        return {"message": "Verification email sent successfully"}
        
    except Exception as e:
        logger.error(f"Error sending verification email: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send verification email: {str(e)}"
        )


@router.post("/users/{user_id}/verify-email")
@limiter.limit("50/minute")
async def verify_user_email(
    request: Request,
    user_id: str,
    admin_user: User = Depends(require_csrf_protection),
    db: Session = Depends(get_db)
):
    """
    Manually verify a user's email address (admin only).
    
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
    
    if user.is_verified:
        return {"message": "User email is already verified"}
    
    # Mark email as verified
    user.is_verified = True
    db.commit()
    db.refresh(user)
    
    # SECURITY: Log admin action
    logger.info(
        f"Admin action: verify_user_email",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "target_user_id": user_id,
            "target_email": user.email,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    # Create audit log entry
    audit_log = UserAuditLog(
        user_id=user.id,
        action="admin_verify_email",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
        details=f"Email verified by admin: {admin_user.email}"
    )
    db.add(audit_log)
    db.commit()
    
    return {"message": "User email verified successfully"}


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
            "created_at": format_utc_datetime(sub.created_at),
            "current_period_start": format_utc_datetime(sub.current_period_start),
            "current_period_end": format_utc_datetime(sub.current_period_end),
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
            "created_at": format_utc_datetime(thread.created_at),
            "updated_at": format_utc_datetime(thread.updated_at),
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
        "created_at": format_utc_datetime(thread.created_at),
        "updated_at": format_utc_datetime(thread.updated_at),
        "messages": [
            {
                "id": msg.id,
                "content": msg.content,
                "sender": msg.sender.value,
                "read": msg.read,
                "created_at": format_utc_datetime(msg.created_at),
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


# ===== Audit Logs =====

@router.get("/audit-logs", response_model=AuditLogListResponse)
@limiter.limit("100/minute")
async def list_audit_logs(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    action: Optional[str] = Query(None, description="Filter by action type"),
    start_date: Optional[str] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="End date (ISO format)"),
    search: Optional[str] = Query(None, max_length=100, description="Search in details or user agent"),
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    List audit logs with filtering and pagination.
    
    **Admin only**
    
    **Query Parameters:**
    - `skip`: Number of records to skip (default: 0)
    - `limit`: Number of records to return (default: 50, max: 100)
    - `user_id`: Filter by user ID
    - `action`: Filter by action type (e.g., "register", "login", "update_profile")
    - `start_date`: Filter logs from this date (ISO format)
    - `end_date`: Filter logs until this date (ISO format)
    - `search`: Search in details or user agent (max 100 characters)
    
    **Response 200**: List of audit logs
    **Response 403**: Not an admin
    """
    # Build query
    query = db.query(UserAuditLog)
    
    # Apply filters
    if user_id:
        query = query.filter(UserAuditLog.user_id == user_id)
    
    if action:
        # Sanitize action input (limit to 50 chars, alphanumeric and underscore only)
        sanitized_action = ''.join(c for c in action[:50] if c.isalnum() or c == '_')
        if sanitized_action:
            query = query.filter(UserAuditLog.action == sanitized_action)
    
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            query = query.filter(UserAuditLog.created_at >= start_dt)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid start_date format. Use ISO format."
            )
    
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            query = query.filter(UserAuditLog.created_at <= end_dt)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid end_date format. Use ISO format."
            )
    
    if search:
        # Sanitize search input
        sanitized_search = search[:100].strip()
        if sanitized_search:
            search_filter = or_(
                UserAuditLog.details.ilike(f"%{sanitized_search}%"),
                UserAuditLog.user_agent.ilike(f"%{sanitized_search}%")
            )
            query = query.filter(search_filter)
    
    # Get total count
    total = query.count()
    
    # Apply pagination and ordering
    logs = query.order_by(UserAuditLog.created_at.desc()).offset(skip).limit(limit).all()
    
    # Format response
    logs_data = []
    for log in logs:
        # Get user email for display
        user = db.query(User).filter(User.id == log.user_id).first()
        user_email = user.email if user else None
        
        log_dict = log.to_dict()
        log_dict["user_email"] = user_email
        logs_data.append(log_dict)
    
    # SECURITY: Log admin action
    logger.info(
        f"Admin action: list_audit_logs",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "filters": {
                "user_id": user_id,
                "action": action,
                "start_date": start_date,
                "end_date": end_date,
                "search": search[:50] if search else None,
            },
            "result_count": len(logs_data),
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    return {
        "logs": logs_data,
        "total": total,
        "page": (skip // limit) + 1,
        "limit": limit
    }


@router.get("/audit-logs/{log_id}")
@limiter.limit("100/minute")
async def get_audit_log(
    request: Request,
    log_id: str,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    Get a specific audit log entry.
    
    **Admin only**
    
    **Response 200**: Audit log details
    **Response 404**: Log not found
    **Response 403**: Not an admin
    """
    log = db.query(UserAuditLog).filter(UserAuditLog.id == log_id).first()
    
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit log not found"
        )
    
    # Get user details
    user = db.query(User).filter(User.id == log.user_id).first()
    
    log_dict = log.to_dict()
    log_dict["user_email"] = user.email if user else None
    log_dict["user_full_name"] = user.full_name if user else None
    
    return log_dict


# ===== Page Visits =====

@router.get("/page-visits", response_model=PageVisitListResponse)
@limiter.limit("100/minute")
async def list_page_visits(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    page_path: Optional[str] = Query(None, description="Filter by page path"),
    utm_source: Optional[str] = Query(None, description="Filter by UTM source"),
    utm_medium: Optional[str] = Query(None, description="Filter by UTM medium"),
    utm_campaign: Optional[str] = Query(None, description="Filter by UTM campaign"),
    device_type: Optional[str] = Query(None, description="Filter by device type"),
    country: Optional[str] = Query(None, max_length=2, description="Filter by country code (ISO 3166-1 alpha-2)"),
    start_date: Optional[str] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="End date (ISO format)"),
    search: Optional[str] = Query(None, max_length=100, description="Search in referrer or user agent"),
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    List page visits with filtering and pagination.
    
    **Admin only**
    
    **Query Parameters:**
    - `skip`: Number of records to skip (default: 0)
    - `limit`: Number of records to return (default: 50, max: 100)
    - `page_path`: Filter by page path (e.g., "/", "/pricing")
    - `utm_source`: Filter by UTM source
    - `utm_medium`: Filter by UTM medium
    - `utm_campaign`: Filter by UTM campaign
    - `device_type`: Filter by device type (mobile, tablet, desktop)
    - `country`: Filter by country code (ISO 3166-1 alpha-2, e.g., 'US', 'GB')
    - `start_date`: Filter visits from this date (ISO format)
    - `end_date`: Filter visits until this date (ISO format)
    - `search`: Search in referrer or user agent (max 100 characters)
    
    **Response 200**: List of page visits
    **Response 403**: Not an admin
    """
    # Build query
    query = db.query(PageVisit)
    
    # Apply filters
    if page_path:
        query = query.filter(PageVisit.page_path == page_path)
    
    if utm_source:
        sanitized_source = ''.join(c for c in utm_source[:100] if c.isalnum() or c in ['-', '_'])
        if sanitized_source:
            query = query.filter(PageVisit.utm_source == sanitized_source)
    
    if utm_medium:
        sanitized_medium = ''.join(c for c in utm_medium[:100] if c.isalnum() or c in ['-', '_'])
        if sanitized_medium:
            query = query.filter(PageVisit.utm_medium == sanitized_medium)
    
    if utm_campaign:
        sanitized_campaign = ''.join(c for c in utm_campaign[:100] if c.isalnum() or c in ['-', '_'])
        if sanitized_campaign:
            query = query.filter(PageVisit.utm_campaign == sanitized_campaign)
    
    if device_type:
        if device_type in ['mobile', 'tablet', 'desktop']:
            query = query.filter(PageVisit.device_type == device_type)
    
    if country:
        # Sanitize country code (2 uppercase letters)
        sanitized_country = ''.join(c for c in country[:2].upper() if c.isalpha())
        if sanitized_country and len(sanitized_country) == 2:
            query = query.filter(PageVisit.country == sanitized_country)
    
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            query = query.filter(PageVisit.created_at >= start_dt)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid start_date format. Use ISO format."
            )
    
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            query = query.filter(PageVisit.created_at <= end_dt)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid end_date format. Use ISO format."
            )
    
    if search:
        # Sanitize search input
        sanitized_search = search[:100].strip()
        if sanitized_search:
            search_filter = or_(
                PageVisit.referrer.ilike(f"%{sanitized_search}%"),
                PageVisit.user_agent.ilike(f"%{sanitized_search}%")
            )
            query = query.filter(search_filter)
    
    # Get total count
    total = query.count()
    
    # Apply pagination and ordering
    visits = query.order_by(PageVisit.created_at.desc()).offset(skip).limit(limit).all()
    
    # Format response
    visits_data = []
    for visit in visits:
        visit_dict = visit.to_dict()
        # Get user email if user_id exists
        if visit.user_id:
            user = db.query(User).filter(User.id == visit.user_id).first()
            visit_dict["user_email"] = user.email if user else None
        else:
            visit_dict["user_email"] = None
        visits_data.append(visit_dict)
    
    # SECURITY: Log admin action
    logger.info(
        f"Admin action: list_page_visits",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "filters": {
                "page_path": page_path,
                "utm_source": utm_source,
                "device_type": device_type,
                "start_date": start_date,
                "end_date": end_date,
                "search": search[:50] if search else None,
            },
            "result_count": len(visits_data),
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    return {
        "visits": visits_data,
        "total": total,
        "page": (skip // limit) + 1,
        "limit": limit
    }


@router.get("/page-visits/stats")
@limiter.limit("100/minute")
async def get_page_visit_stats(
    request: Request,
    start_date: Optional[str] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="End date (ISO format)"),
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    Get page visit statistics.
    
    Returns aggregated statistics about page visits.
    
    **Admin only**
    
    **Response 200**: Visit statistics
    **Response 403**: Not an admin
    """
    try:
        query = db.query(PageVisit)
        
        # Apply date filters
        if start_date:
            try:
                start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                query = query.filter(PageVisit.created_at >= start_dt)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid start_date format. Use ISO format."
                )
        
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                query = query.filter(PageVisit.created_at <= end_dt)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid end_date format. Use ISO format."
                )
        
        # Get counts
        total_visits = query.count()
        
        # Get unique IPs
        unique_ips = db.query(PageVisit.ip_address).distinct().count()
        
        # Get unique sessions
        unique_sessions = db.query(PageVisit.session_id).filter(PageVisit.session_id.isnot(None)).distinct().count()
        
        # Get top pages
        from sqlalchemy import func
        top_pages = (
            db.query(
                PageVisit.page_path,
                func.count(PageVisit.id).label('count')
            )
            .group_by(PageVisit.page_path)
            .order_by(func.count(PageVisit.id).desc())
            .limit(10)
            .all()
        )
        
        # Get top referrers
        top_referrers = (
            db.query(
                PageVisit.referrer,
                func.count(PageVisit.id).label('count')
            )
            .filter(PageVisit.referrer.isnot(None))
            .group_by(PageVisit.referrer)
            .order_by(func.count(PageVisit.id).desc())
            .limit(10)
            .all()
        )
        
        # Get device type breakdown
        device_breakdown = (
            db.query(
                PageVisit.device_type,
                func.count(PageVisit.id).label('count')
            )
            .filter(PageVisit.device_type.isnot(None))
            .group_by(PageVisit.device_type)
            .all()
        )
        
        # Get UTM source breakdown
        utm_source_breakdown = (
            db.query(
                PageVisit.utm_source,
                func.count(PageVisit.id).label('count')
            )
            .filter(PageVisit.utm_source.isnot(None))
            .group_by(PageVisit.utm_source)
            .order_by(func.count(PageVisit.id).desc())
            .limit(10)
            .all()
        )
        
        return {
            "total_visits": total_visits,
            "unique_ips": unique_ips,
            "unique_sessions": unique_sessions,
            "top_pages": [{"page_path": path, "count": count} for path, count in top_pages],
            "top_referrers": [{"referrer": ref, "count": count} for ref, count in top_referrers],
            "device_breakdown": [{"device_type": device, "count": count} for device, count in device_breakdown],
            "utm_source_breakdown": [{"utm_source": source, "count": count} for source, count in utm_source_breakdown],
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting page visit stats: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get page visit statistics"
        )


# ===== Service Status and Health Checks =====

class ServiceStatusResponse(BaseModel):
    """Service status response model."""
    service: str
    status: str  # "healthy", "degraded", "down", "unknown"
    message: str
    response_time_ms: Optional[float] = None
    details: Optional[Dict[str, Any]] = None
    last_checked: str


class SystemStatusResponse(BaseModel):
    """System status response model."""
    overall_status: str  # "healthy", "degraded", "down"
    services: List[ServiceStatusResponse]
    checked_at: str


@router.get("/status", response_model=SystemStatusResponse)
@limiter.limit("30/minute")
async def get_system_status(
    request: Request,
    admin_user: User = Depends(get_admin_user)
):
    """
    Get comprehensive system status for all services.
    
    Checks health of:
    - PostgreSQL Database
    - Redis Cache
    - SMTP Email Service
    - Paddle Payment Gateway
    - Rixly API
    - Sentry Monitoring
    
    **Admin Only**: Requires admin role.
    
    **Response 200**: System status with all service health checks
    **Response 403**: Not an admin
    """
    services_status = []
    overall_status = "healthy"
    
    # Helper function to check service
    async def check_service(name: str, check_func, *args, **kwargs):
        start_time = time.time()
        try:
            if asyncio.iscoroutinefunction(check_func):
                result = await check_func(*args, **kwargs)
            else:
                result = check_func(*args, **kwargs)
            response_time = (time.time() - start_time) * 1000  # Convert to ms
            
            if result.get("status") == "healthy":
                return {
                    "service": name,
                    "status": "healthy",
                    "message": result.get("message", "Service is operational"),
                    "response_time_ms": round(response_time, 2),
                    "details": result.get("details"),
                    "last_checked": datetime.utcnow().isoformat()
                }
            else:
                return {
                    "service": name,
                    "status": result.get("status", "degraded"),
                    "message": result.get("message", "Service check failed"),
                    "response_time_ms": round(response_time, 2),
                    "details": result.get("details"),
                    "last_checked": datetime.utcnow().isoformat()
                }
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(f"Error checking {name}: {str(e)}", exc_info=True)
            return {
                "service": name,
                "status": "down",
                "message": f"Service check failed: {str(e)}",
                "response_time_ms": round(response_time, 2),
                "details": {"error": str(e)},
                "last_checked": datetime.utcnow().isoformat()
            }
    
    # Check PostgreSQL Database
    def check_database():
        try:
            start = time.time()
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                result.fetchone()
                response_time = (time.time() - start) * 1000
                
                # Get database info
                db_info = conn.execute(text("SELECT version()")).fetchone()
                version = db_info[0] if db_info else "Unknown"
                
                # Get connection pool info
                pool = engine.pool
                pool_size = pool.size() if hasattr(pool, 'size') else None
                checked_out = pool.checkedout() if hasattr(pool, 'checkedout') else None
                
                return {
                    "status": "healthy",
                    "message": "Database connection successful",
                    "details": {
                        "version": version.split(',')[0] if version else "Unknown",
                        "response_time_ms": round(response_time, 2),
                        "pool_size": pool_size,
                        "checked_out": checked_out
                    }
                }
        except Exception as e:
            return {
                "status": "down",
                "message": f"Database connection failed: {str(e)}",
                "details": {"error": str(e)}
            }
    
    # Check Redis
    def check_redis():
        try:
            start = time.time()
            if is_redis_available():
                client = get_redis_client()
                if client:
                    # Get Redis info
                    info = client.info()
                    response_time = (time.time() - start) * 1000
                    
                    return {
                        "status": "healthy",
                        "message": "Redis connection successful",
                        "details": {
                            "version": info.get("redis_version", "Unknown"),
                            "used_memory_human": info.get("used_memory_human", "Unknown"),
                            "connected_clients": info.get("connected_clients", 0),
                            "response_time_ms": round(response_time, 2)
                        }
                    }
            return {
                "status": "down",
                "message": "Redis is not available",
                "details": {"note": "Application will use in-memory fallback"}
            }
        except Exception as e:
            return {
                "status": "down",
                "message": f"Redis check failed: {str(e)}",
                "details": {"error": str(e)}
            }
    
    # Check SMTP
    def check_smtp():
        try:
            if not settings.SMTP_HOST or not settings.SMTP_USER or not settings.SMTP_PASSWORD:
                return {
                    "status": "unknown",
                    "message": "SMTP not configured",
                    "details": {"note": "SMTP credentials not set"}
                }
            
            start = time.time()
            host = settings.SMTP_HOST
            port = settings.SMTP_PORT
            
            # Try to connect (don't authenticate to avoid rate limits)
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=5)
            else:
                server = smtplib.SMTP(host, port, timeout=5)
                server.starttls()
            
            # Just check connection, don't login (to avoid rate limits)
            server.quit()
            response_time = (time.time() - start) * 1000
            
            return {
                "status": "healthy",
                "message": "SMTP server connection successful",
                "details": {
                    "host": host,
                    "port": port,
                    "response_time_ms": round(response_time, 2)
                }
            }
        except smtplib.SMTPConnectError as e:
            return {
                "status": "down",
                "message": f"SMTP connection failed: {str(e)}",
                "details": {"error": str(e), "host": settings.SMTP_HOST, "port": settings.SMTP_PORT}
            }
        except Exception as e:
            return {
                "status": "degraded",
                "message": f"SMTP check failed: {str(e)}",
                "details": {"error": str(e)}
            }
    
    # Check Paddle
    async def check_paddle():
        try:
            if not settings.PADDLE_ENABLED:
                return {
                    "status": "unknown",
                    "message": "Paddle is disabled",
                    "details": {"note": "Paddle payment gateway is not enabled"}
                }
            
            if not settings.PADDLE_API_KEY or not settings.PADDLE_VENDOR_ID:
                return {
                    "status": "unknown",
                    "message": "Paddle not configured",
                    "details": {"note": "Paddle API credentials not set"}
                }
            
            start = time.time()
            # Try to make a simple API call to Paddle
            # Use Paddle's products endpoint as a health check
            api_url = "https://sandbox-api.paddle.com" if settings.PADDLE_ENVIRONMENT == "sandbox" else "https://api.paddle.com"
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{api_url}/products",
                    headers={
                        "Authorization": f"Bearer {settings.PADDLE_API_KEY}",
                        "Content-Type": "application/json"
                    }
                )
                response_time = (time.time() - start) * 1000
                
                if response.status_code == 200:
                    return {
                        "status": "healthy",
                        "message": "Paddle API connection successful",
                        "details": {
                            "environment": settings.PADDLE_ENVIRONMENT,
                            "response_time_ms": round(response_time, 2)
                        }
                    }
                elif response.status_code == 401:
                    return {
                        "status": "down",
                        "message": "Paddle API authentication failed",
                        "details": {"error": "Invalid API credentials"}
                    }
                else:
                    return {
                        "status": "degraded",
                        "message": f"Paddle API returned status {response.status_code}",
                        "details": {"status_code": response.status_code}
                    }
        except httpx.TimeoutException:
            return {
                "status": "down",
                "message": "Paddle API timeout",
                "details": {"error": "Request timed out"}
            }
        except Exception as e:
            return {
                "status": "down",
                "message": f"Paddle check failed: {str(e)}",
                "details": {"error": str(e)}
            }
    
    # Check Rixly API
    async def check_rixly():
        try:
            if not settings.RIXLY_API_URL or not settings.RIXLY_API_KEY:
                return {
                    "status": "unknown",
                    "message": "Rixly not configured",
                    "details": {"note": "Rixly API credentials not set"}
                }
            
            start = time.time()
            api_url = settings.RIXLY_API_URL.rstrip('/')
            
            # Try to call Rixly health endpoint or a simple endpoint
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Try health endpoint first, fallback to API root
                try:
                    response = await client.get(
                        f"{api_url}/health",
                        headers={"X-API-Key": settings.RIXLY_API_KEY},
                        timeout=5.0
                    )
                except:
                    # Fallback to API info endpoint
                    response = await client.get(
                        f"{api_url}/api/v1/info",
                        headers={"X-API-Key": settings.RIXLY_API_KEY},
                        timeout=5.0
                    )
                
                response_time = (time.time() - start) * 1000
                
                if response.status_code == 200:
                    return {
                        "status": "healthy",
                        "message": "Rixly API connection successful",
                        "details": {
                            "url": api_url,
                            "response_time_ms": round(response_time, 2)
                        }
                    }
                elif response.status_code == 401:
                    return {
                        "status": "down",
                        "message": "Rixly API authentication failed",
                        "details": {"error": "Invalid API key"}
                    }
                else:
                    return {
                        "status": "degraded",
                        "message": f"Rixly API returned status {response.status_code}",
                        "details": {"status_code": response.status_code, "url": api_url}
                    }
        except httpx.TimeoutException:
            return {
                "status": "down",
                "message": "Rixly API timeout",
                "details": {"error": "Request timed out", "url": settings.RIXLY_API_URL}
            }
        except httpx.ConnectError:
            return {
                "status": "down",
                "message": "Rixly API connection failed",
                "details": {"error": "Cannot connect to Rixly API", "url": settings.RIXLY_API_URL}
            }
        except Exception as e:
            return {
                "status": "down",
                "message": f"Rixly check failed: {str(e)}",
                "details": {"error": str(e)}
            }
    
    # Check Sentry
    def check_sentry():
        try:
            if not settings.SENTRY_ENABLED:
                return {
                    "status": "unknown",
                    "message": "Sentry is disabled",
                    "details": {"note": "Error monitoring is not enabled"}
                }
            
            if not settings.SENTRY_DSN:
                return {
                    "status": "unknown",
                    "message": "Sentry not configured",
                    "details": {"note": "Sentry DSN not set"}
                }
            
            # Sentry doesn't have a health check endpoint, so we just verify config
            return {
                "status": "healthy",
                "message": "Sentry is configured",
                "details": {
                    "enabled": settings.SENTRY_ENABLED,
                    "dsn_configured": bool(settings.SENTRY_DSN)
                }
            }
        except Exception as e:
            return {
                "status": "unknown",
                "message": f"Sentry check failed: {str(e)}",
                "details": {"error": str(e)}
            }
    
    # Run all checks
    db_status = await check_service("PostgreSQL Database", check_database)
    services_status.append(ServiceStatusResponse(**db_status))
    if db_status["status"] != "healthy":
        overall_status = "degraded" if overall_status == "healthy" else "down"
    
    redis_status = await check_service("Redis Cache", check_redis)
    services_status.append(ServiceStatusResponse(**redis_status))
    if redis_status["status"] == "down":
        overall_status = "degraded" if overall_status == "healthy" else "down"
    
    smtp_status = await check_service("SMTP Email Service", check_smtp)
    services_status.append(ServiceStatusResponse(**smtp_status))
    if smtp_status["status"] == "down":
        overall_status = "degraded"
    
    paddle_status = await check_service("Paddle Payment Gateway", check_paddle)
    services_status.append(ServiceStatusResponse(**paddle_status))
    if paddle_status["status"] == "down":
        overall_status = "degraded"
    
    rixly_status = await check_service("Rixly API", check_rixly)
    services_status.append(ServiceStatusResponse(**rixly_status))
    if rixly_status["status"] == "down":
        overall_status = "degraded"
    
    sentry_status = await check_service("Sentry Monitoring", check_sentry)
    services_status.append(ServiceStatusResponse(**sentry_status))
    
    # SECURITY: Log admin action
    logger.info(
        f"Admin action: get_system_status",
        extra={
            "admin_user_id": admin_user.id,
            "admin_email": admin_user.email,
            "overall_status": overall_status,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    return SystemStatusResponse(
        overall_status=overall_status,
        services=services_status,
        checked_at=datetime.utcnow().isoformat()
    )
