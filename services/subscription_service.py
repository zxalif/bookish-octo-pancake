"""
Subscription Service

Handles subscription management business logic:
- Get active subscription
- Check subscription limits
- Activate/cancel subscriptions
- Plan management
"""

from typing import Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from models.user import User
from models.subscription import Subscription, SubscriptionStatus, SubscriptionPlan
from models.price import BillingPeriod
from services.price_service import PriceService
from core.config import get_settings

settings = get_settings()


class SubscriptionService:
    """Service for handling subscription operations."""
    
    @staticmethod
    def get_active_subscription(user_id: str, db: Session) -> Optional[Subscription]:
        """
        Get user's active subscription.
        
        For free tier, checks if 1 month has passed and expires if needed.
        
        Args:
            user_id: User UUID
            db: Database session
            
        Returns:
            Subscription: Active subscription if found, None otherwise
        """
        subscription = db.query(Subscription).filter(
            Subscription.user_id == user_id,
            Subscription.status == SubscriptionStatus.ACTIVE
        ).first()
        
        if not subscription:
            return None
        
        # Check if free tier has expired (1 month)
        if subscription.plan == SubscriptionPlan.FREE:
            if subscription.current_period_end and subscription.current_period_end < datetime.utcnow():
                # Free tier expired, mark as expired
                subscription.status = SubscriptionStatus.EXPIRED
                db.commit()
                return None
        
        return subscription
    
    @staticmethod
    def get_subscription_by_id(subscription_id: str, user_id: str, db: Session) -> Optional[Subscription]:
        """
        Get subscription by ID (user-scoped).
        
        Args:
            subscription_id: Subscription UUID
            user_id: User UUID (for security)
            db: Database session
            
        Returns:
            Subscription: Subscription if found and belongs to user, None otherwise
        """
        return db.query(Subscription).filter(
            Subscription.id == subscription_id,
            Subscription.user_id == user_id
        ).first()
    
    @staticmethod
    def get_plan_limits(plan: str) -> dict:
        """
        Get plan limits from configuration.
        
        Args:
            plan: Plan name (starter, professional, power)
            
        Returns:
            dict: Plan limits
            
        Raises:
            ValueError: If plan not found
        """
        limits = settings.PLAN_LIMITS.get(plan)
        if limits is None:
            raise ValueError(f"Unknown plan: {plan}")
        return limits
    
    @staticmethod
    def check_usage_limit(
        user_id: str,
        metric_type: str,
        db: Session
    ) -> tuple[bool, int, int]:
        """
        Check if user has reached usage limit for a metric.
        
        IMPORTANT: 
        - keyword_searches: CONCURRENT limit (count active searches)
        - opportunities_per_month: MONTHLY limit (count in current period)
        - api_calls_per_month: MONTHLY limit (count in current period)
        
        Args:
            user_id: User UUID
            metric_type: Type of metric (keyword_searches, opportunities_per_month, api_calls_per_month)
            db: Database session
            
        Returns:
            tuple: (allowed, current_count, limit)
                - allowed: True if under limit, False if limit reached
                - current_count: Current usage count
                - limit: Plan limit for this metric
        """
        # Get active subscription
        subscription = SubscriptionService.get_active_subscription(user_id, db)
        if not subscription:
            return (False, 0, 0)  # No subscription = no access
        
        # Get plan limits
        plan_limits = SubscriptionService.get_plan_limits(subscription.plan.value)
        limit = plan_limits.get(metric_type, 0)
        
        # Free tier is now limited to power user limits (10/500), so no special handling needed
        # Limits are enforced normally like other plans
        
        if metric_type == "keyword_searches":
            # CONCURRENT limit - count active + soft-deleted searches in current month
            # This prevents abuse: deleted searches still count until next month
            from models.keyword_search import KeywordSearch
            from datetime import datetime, timedelta
            
            # Get current month period
            now = datetime.utcnow()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            # Count: active searches OR soft-deleted searches from current month
            current_count = db.query(KeywordSearch).filter(
                KeywordSearch.user_id == user_id,
                # Active searches
                (
                    (KeywordSearch.enabled == True) & (KeywordSearch.deleted_at.is_(None))  # type: ignore
                ) | (
                    # OR soft-deleted searches from current month (still count toward limit)
                    (KeywordSearch.deleted_at.isnot(None)) &  # type: ignore
                    (KeywordSearch.deleted_at >= month_start)  # type: ignore
                )
            ).count()
        elif metric_type == "keyword_searches_created_per_month":
            # MONTHLY creation limit - track total searches created this month
            from models.usage_metric import UsageMetric
            from datetime import datetime, timedelta
            
            # Get current period (monthly)
            now = datetime.utcnow()
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            period_end = (period_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
            
            usage_metric = db.query(UsageMetric).filter(
                UsageMetric.user_id == user_id,
                UsageMetric.subscription_id == subscription.id,
                UsageMetric.metric_type == metric_type,
                UsageMetric.period_start == period_start
            ).first()
            
            current_count = usage_metric.count if usage_metric else 0
        else:
            # MONTHLY limit - get from usage metrics
            from models.usage_metric import UsageMetric
            from datetime import datetime
            
            # Get current period (monthly)
            now = datetime.utcnow()
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            period_end = (period_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
            
            usage_metric = db.query(UsageMetric).filter(
                UsageMetric.user_id == user_id,
                UsageMetric.subscription_id == subscription.id,
                UsageMetric.metric_type == metric_type,
                UsageMetric.period_start == period_start
            ).first()
            
            current_count = usage_metric.count if usage_metric else 0
        
        allowed = current_count < limit
        return (allowed, current_count, limit)
    
    @staticmethod
    def create_subscription(
        user_id: str,
        plan: str,
        billing_period: str = "monthly",
        paddle_subscription_id: Optional[str] = None,
        price_id: Optional[str] = None,
        db: Session = None
    ) -> Subscription:
        """
        Create a new subscription for user.
        
        Args:
            user_id: User UUID
            plan: Plan name (starter, professional, power)
            paddle_subscription_id: Optional Paddle subscription ID
            db: Database session
            
        Returns:
            Subscription: Created subscription
            
        Raises:
            ValueError: If plan is invalid
            HTTPException: If user already has active subscription
        """
        # Validate plan
        try:
            plan_enum = SubscriptionPlan(plan)
        except ValueError:
            raise ValueError(f"Invalid plan: {plan}. Must be one of: free, starter, professional, power")
        
        # For free plan, allow creating even if user has active subscription
        # (they might be upgrading from free to paid, or re-creating free)
        if plan_enum != SubscriptionPlan.FREE:
            # Check if user already has active subscription (for paid plans)
            existing = SubscriptionService.get_active_subscription(user_id, db)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User already has an active subscription"
                )
        
        # Get or validate billing period
        try:
            billing_period_enum = BillingPeriod(billing_period.lower())
        except ValueError:
            billing_period_enum = BillingPeriod.MONTHLY  # Default to monthly
        
        # Get price from database if price_id not provided
        if not price_id:
            price = PriceService.get_price_by_plan_and_period(plan, billing_period_enum.value, db)
            if price:
                price_id = price.id
        
        # Calculate billing period dates
        now = datetime.utcnow()
        
        # For free plan, set 1-month period (30 days)
        if plan_enum == SubscriptionPlan.FREE:
            period_start = now
            period_end = now + timedelta(days=30)  # 1 month
        elif billing_period_enum == BillingPeriod.MONTHLY:
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            period_end = (period_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
        else:  # YEARLY
            period_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            period_end = period_start.replace(year=period_start.year + 1) - timedelta(seconds=1)
        
        # Create subscription
        subscription = Subscription(
            user_id=user_id,
            plan=plan_enum,
            billing_period=billing_period_enum,
            price_id=price_id,
            status=SubscriptionStatus.ACTIVE,
            paddle_subscription_id=paddle_subscription_id,
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=False  # Fixed: Now Boolean
        )
        
        db.add(subscription)
        db.commit()
        db.refresh(subscription)
        
        return subscription
    
    @staticmethod
    def create_free_subscription(
        user_id: str,
        db: Session
    ) -> Subscription:
        """
        Automatically create free subscription for new user.
        
        Free subscription lasts 1 month (30 days) from creation.
        This is used for the validation period.
        
        Args:
            user_id: User UUID
            db: Database session
            
        Returns:
            Subscription: Created free subscription
            
        Note:
            If user already has an active subscription, returns existing subscription.
            If user has expired free subscription, creates a new one.
        """
        # Check if user already has active subscription
        existing = SubscriptionService.get_active_subscription(user_id, db)
        if existing:
            return existing  # Already has active subscription
        
        # Calculate 1-month period
        now = datetime.utcnow()
        period_start = now
        period_end = now + timedelta(days=30)  # 1 month
        
        # Create free subscription
        subscription = Subscription(
            user_id=user_id,
            plan=SubscriptionPlan.FREE,
            billing_period=BillingPeriod.MONTHLY,  # Not used for free, but required
            price_id=None,  # No price for free tier
            status=SubscriptionStatus.ACTIVE,
            paddle_subscription_id=None,  # No Paddle for free tier
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=False
        )
        
        db.add(subscription)
        db.commit()
        db.refresh(subscription)
        
        return subscription
    
    @staticmethod
    def cancel_subscription(
        subscription_id: str,
        user_id: str,
        cancel_at_period_end: bool = True,
        db: Session = None
    ) -> Subscription:
        """
        Cancel a subscription.
        
        Args:
            subscription_id: Subscription UUID
            user_id: User UUID (for security)
            cancel_at_period_end: If True, cancel at period end; if False, cancel immediately
            db: Database session
            
        Returns:
            Subscription: Updated subscription
            
        Raises:
            HTTPException: If subscription not found or doesn't belong to user
        """
        subscription = SubscriptionService.get_subscription_by_id(subscription_id, user_id, db)
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription not found"
            )
        
        if cancel_at_period_end:
            # Cancel at period end
            subscription.cancel_at_period_end = True  # Fixed: Now Boolean
            subscription.status = SubscriptionStatus.ACTIVE  # Keep active until period end
        else:
            # Cancel immediately
            subscription.status = SubscriptionStatus.CANCELLED
            subscription.cancel_at_period_end = False  # Fixed: Now Boolean
        
        db.commit()
        db.refresh(subscription)
        
        return subscription
    
    @staticmethod
    def update_subscription_status(
        subscription_id: str,
        status: SubscriptionStatus,
        db: Session
    ) -> Subscription:
        """
        Update subscription status (typically called by webhook).
        
        Args:
            subscription_id: Subscription UUID
            status: New status
            db: Database session
            
        Returns:
            Subscription: Updated subscription
        """
        subscription = db.query(Subscription).filter(
            Subscription.id == subscription_id
        ).first()
        
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription not found"
            )
        
        subscription.status = status
        db.commit()
        db.refresh(subscription)
        
        return subscription

