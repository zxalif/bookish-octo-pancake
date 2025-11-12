"""
Usage Service

Handles usage tracking business logic:
- Increment usage metrics
- Get current usage
- Reset usage limits
- Check usage limits
"""

from typing import Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from models.user import User
from models.subscription import Subscription
from models.usage_metric import UsageMetric
from services.subscription_service import SubscriptionService


class UsageService:
    """Service for handling usage tracking operations."""
    
    @staticmethod
    def get_current_usage(
        user_id: str,
        subscription_id: str,
        metric_type: str,
        db: Session
    ) -> UsageMetric:
        """
        Get or create current usage metric for a period.
        
        Args:
            user_id: User UUID
            subscription_id: Subscription UUID
            metric_type: Type of metric (opportunities_per_month, api_calls_per_month)
            db: Database session
            
        Returns:
            UsageMetric: Current usage metric
        """
        # Get current period (monthly)
        now = datetime.utcnow()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_end = (period_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
        
        # Get or create usage metric
        usage_metric = db.query(UsageMetric).filter(
            UsageMetric.user_id == user_id,
            UsageMetric.subscription_id == subscription_id,
            UsageMetric.metric_type == metric_type,
            UsageMetric.period_start == period_start
        ).first()
        
        if not usage_metric:
            # Create new usage metric for this period
            usage_metric = UsageMetric(
                user_id=user_id,
                subscription_id=subscription_id,
                metric_type=metric_type,
                count=0,
                period_start=period_start,
                period_end=period_end
            )
            db.add(usage_metric)
            db.commit()
            db.refresh(usage_metric)
        
        return usage_metric
    
    @staticmethod
    def increment_usage(
        user_id: str,
        subscription_id: str,
        metric_type: str,
        amount: int = 1,
        db: Session = None
    ) -> UsageMetric:
        """
        Increment usage metric.
        
        Args:
            user_id: User UUID
            subscription_id: Subscription UUID
            metric_type: Type of metric (opportunities_per_month, api_calls_per_month)
            amount: Amount to increment (default: 1)
            db: Database session
            
        Returns:
            UsageMetric: Updated usage metric
        """
        usage_metric = UsageService.get_current_usage(user_id, subscription_id, metric_type, db)
        
        usage_metric.count += amount
        db.commit()
        db.refresh(usage_metric)
        
        return usage_metric
    
    @staticmethod
    def reset_usage_limits(
        subscription_id: str,
        db: Session
    ) -> None:
        """
        Reset usage limits for expired periods only (called at period start).
        
        IMPORTANT: 
        - Only resets metrics from previous periods
        - Current period metrics are NOT reset (they auto-reset via get_current_usage)
        - Keyword searches are NOT affected (they're concurrent limits, not monthly)
        
        Args:
            subscription_id: Subscription UUID
            db: Database session
        """
        # Get subscription
        subscription = db.query(Subscription).filter(
            Subscription.id == subscription_id
        ).first()
        
        if not subscription:
            return
        
        # Get current period (monthly)
        now = datetime.utcnow()
        current_period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Only reset/delete metrics from PREVIOUS periods
        # Current period metrics are handled automatically by get_current_usage()
        expired_metrics = db.query(UsageMetric).filter(
            UsageMetric.subscription_id == subscription_id,
            UsageMetric.period_start < current_period_start
        ).all()
        
        # Delete expired metrics (or could archive to history table)
        for metric in expired_metrics:
            db.delete(metric)
        
        db.commit()
    
    @staticmethod
    def get_all_usage(
        user_id: str,
        subscription_id: str,
        db: Session
    ) -> dict:
        """
        Get all usage metrics for a subscription.
        
        Args:
            user_id: User UUID
            subscription_id: Subscription UUID
            db: Database session
            
        Returns:
            dict: Usage metrics by type
        """
        subscription = db.query(Subscription).filter(
            Subscription.id == subscription_id,
            Subscription.user_id == user_id
        ).first()
        
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription not found"
            )
        
        # Get plan limits
        plan_limits = SubscriptionService.get_plan_limits(subscription.plan.value)
        
        # Get current usage for each metric type
        usage_data = {}
        
        for metric_type in ["keyword_searches", "keyword_searches_created_per_month", "opportunities_per_month", "api_calls_per_month"]:
            allowed, current, limit = SubscriptionService.check_usage_limit(
                user_id, metric_type, db
            )
            
            usage_data[metric_type] = {
                "current": current,
                "limit": limit,
                "allowed": allowed,
                "remaining": max(0, limit - current)
            }
        
        return usage_data

