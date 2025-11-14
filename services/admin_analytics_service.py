"""
Admin Analytics Service

Provides analytics and statistics for admin panel:
- User statistics
- Revenue statistics
- Subscription statistics
- Usage statistics
- Growth metrics
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, case
from sqlalchemy import Float as SQLFloat
from decimal import Decimal

from models.user import User
from models.subscription import Subscription, SubscriptionStatus, SubscriptionPlan
from models.payment import Payment, PaymentStatus
from models.usage_metric import UsageMetric
from models.keyword_search import KeywordSearch
from models.opportunity import Opportunity
from core.logger import get_logger

logger = get_logger(__name__)


class AdminAnalyticsService:
    """Service for admin analytics and statistics."""
    
    @staticmethod
    def get_overview_stats(db: Session) -> Dict[str, Any]:
        """
        Get overview statistics for admin dashboard.
        
        Returns:
            dict: Overview statistics including users, subscriptions, revenue, etc.
        """
        now = datetime.utcnow()
        this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)
        last_month_end = this_month_start - timedelta(seconds=1)
        
        # Total users
        total_users = db.query(func.count(User.id)).scalar() or 0
        
        # Active users (verified and active)
        active_users = db.query(func.count(User.id)).filter(
            User.is_active == True,
            User.is_verified == True
        ).scalar() or 0
        
        # New users this month
        new_users_this_month = db.query(func.count(User.id)).filter(
            User.created_at >= this_month_start
        ).scalar() or 0
        
        # New users last month
        new_users_last_month = db.query(func.count(User.id)).filter(
            and_(
                User.created_at >= last_month_start,
                User.created_at <= last_month_end
            )
        ).scalar() or 0
        
        # Active subscriptions
        active_subscriptions = db.query(func.count(Subscription.id)).filter(
            Subscription.status == SubscriptionStatus.ACTIVE
        ).scalar() or 0
        
        # Total subscriptions
        total_subscriptions = db.query(func.count(Subscription.id)).scalar() or 0
        
        # Monthly Recurring Revenue (MRR)
        # Calculate MRR by getting latest payment amount for each active subscription
        # and converting yearly to monthly
        active_subs = db.query(Subscription).filter(
            Subscription.status == SubscriptionStatus.ACTIVE
        ).all()
        
        mrr = Decimal('0')
        for sub in active_subs:
            # Get latest payment for this subscription
            latest_payment = db.query(Payment).filter(
                Payment.subscription_id == sub.id,
                Payment.status == PaymentStatus.COMPLETED
            ).order_by(Payment.created_at.desc()).first()
            
            if latest_payment:
                amount = Decimal(str(latest_payment.amount))
                if sub.billing_period.value == 'yearly':
                    mrr += amount / 12
                elif sub.billing_period.value == 'monthly':
                    mrr += amount
        
        # Total revenue (all-time)
        total_revenue = db.query(func.sum(Payment.amount)).filter(
            Payment.status == PaymentStatus.COMPLETED
        ).scalar() or Decimal('0')
        
        # Revenue this month
        revenue_this_month = db.query(func.sum(Payment.amount)).filter(
            and_(
                Payment.status == PaymentStatus.COMPLETED,
                Payment.created_at >= this_month_start
            )
        ).scalar() or Decimal('0')
        
        # Revenue last month
        revenue_last_month = db.query(func.sum(Payment.amount)).filter(
            and_(
                Payment.status == PaymentStatus.COMPLETED,
                Payment.created_at >= last_month_start,
                Payment.created_at <= last_month_end
            )
        ).scalar() or Decimal('0')
        
        # Calculate growth rates
        user_growth_rate = 0
        if new_users_last_month > 0:
            user_growth_rate = ((new_users_this_month - new_users_last_month) / new_users_last_month) * 100
        
        revenue_growth_rate = 0
        if revenue_last_month > 0:
            revenue_growth_rate = ((float(revenue_this_month) - float(revenue_last_month)) / float(revenue_last_month)) * 100
        
        # Average Revenue Per User (ARPU)
        arpu = 0
        if active_subscriptions > 0:
            arpu = float(mrr) / active_subscriptions
        
        # Churn rate (cancelled subscriptions this month / active subscriptions at start of month)
        cancelled_this_month = db.query(func.count(Subscription.id)).filter(
            and_(
                Subscription.status == SubscriptionStatus.CANCELLED,
                Subscription.updated_at >= this_month_start
            )
        ).scalar() or 0
        
        active_at_month_start = db.query(func.count(Subscription.id)).filter(
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.created_at < this_month_start
            )
        ).scalar() or 0
        
        churn_rate = 0
        if active_at_month_start > 0:
            churn_rate = (cancelled_this_month / active_at_month_start) * 100
        
        return {
            "users": {
                "total": total_users,
                "active": active_users,
                "new_this_month": new_users_this_month,
                "new_last_month": new_users_last_month,
                "growth_rate": round(user_growth_rate, 2)
            },
            "subscriptions": {
                "total": total_subscriptions,
                "active": active_subscriptions,
                "cancelled_this_month": cancelled_this_month
            },
            "revenue": {
                "mrr": float(mrr) / 100,  # Convert cents to dollars
                "total": float(total_revenue) / 100,
                "this_month": float(revenue_this_month) / 100,
                "last_month": float(revenue_last_month) / 100,
                "growth_rate": round(revenue_growth_rate, 2),
                "arpu": round(arpu, 2)
            },
            "metrics": {
                "churn_rate": round(churn_rate, 2)
            }
        }
    
    @staticmethod
    def get_revenue_stats(
        db: Session,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get revenue statistics.
        
        Args:
            db: Database session
            start_date: Start date for filtering (default: 30 days ago)
            end_date: End date for filtering (default: now)
            
        Returns:
            dict: Revenue statistics
        """
        if end_date is None:
            end_date = datetime.utcnow()
        if start_date is None:
            start_date = end_date - timedelta(days=30)
        
        # Revenue by plan
        revenue_by_plan = db.query(
            Subscription.plan,
            func.sum(Payment.amount)
        ).join(
            Payment, Payment.subscription_id == Subscription.id
        ).filter(
            and_(
                Payment.status == PaymentStatus.COMPLETED,
                Payment.created_at >= start_date,
                Payment.created_at <= end_date
            )
        ).group_by(Subscription.plan).all()
        
        # Revenue by billing period
        revenue_by_billing = db.query(
            Subscription.billing_period,
            func.sum(Payment.amount)
        ).join(
            Payment, Payment.subscription_id == Subscription.id
        ).filter(
            and_(
                Payment.status == PaymentStatus.COMPLETED,
                Payment.created_at >= start_date,
                Payment.created_at <= end_date
            )
        ).group_by(Subscription.billing_period).all()
        
        # Revenue over time (daily)
        revenue_over_time = db.query(
            func.date(Payment.created_at).label('date'),
            func.sum(Payment.amount).label('revenue')
        ).filter(
            and_(
                Payment.status == PaymentStatus.COMPLETED,
                Payment.created_at >= start_date,
                Payment.created_at <= end_date
            )
        ).group_by(func.date(Payment.created_at)).order_by('date').all()
        
        # Payment success rate
        total_payments = db.query(func.count(Payment.id)).filter(
            Payment.created_at >= start_date,
            Payment.created_at <= end_date
        ).scalar() or 0
        
        successful_payments = db.query(func.count(Payment.id)).filter(
            and_(
                Payment.status == PaymentStatus.COMPLETED,
                Payment.created_at >= start_date,
                Payment.created_at <= end_date
            )
        ).scalar() or 0
        
        success_rate = 0
        if total_payments > 0:
            success_rate = (successful_payments / total_payments) * 100
        
        # Refund rate
        refunded_payments = db.query(func.count(Payment.id)).filter(
            and_(
                Payment.status == PaymentStatus.REFUNDED,
                Payment.created_at >= start_date,
                Payment.created_at <= end_date
            )
        ).scalar() or 0
        
        refund_rate = 0
        if successful_payments > 0:
            refund_rate = (refunded_payments / successful_payments) * 100
        
        return {
            "revenue_by_plan": {
                plan.value: float(amount) / 100 for plan, amount in revenue_by_plan
            },
            "revenue_by_billing": {
                billing.value: float(amount) / 100 for billing, amount in revenue_by_billing
            },
            "revenue_over_time": [
                {
                    "date": date.isoformat(),
                    "revenue": float(revenue) / 100
                }
                for date, revenue in revenue_over_time
            ],
            "payment_metrics": {
                "total_payments": total_payments,
                "successful_payments": successful_payments,
                "success_rate": round(success_rate, 2),
                "refund_rate": round(refund_rate, 2)
            }
        }
    
    @staticmethod
    def get_user_stats(
        db: Session,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get user statistics.
        
        Args:
            db: Database session
            start_date: Start date for filtering
            end_date: End date for filtering
            
        Returns:
            dict: User statistics
        """
        if end_date is None:
            end_date = datetime.utcnow()
        if start_date is None:
            start_date = end_date - timedelta(days=30)
        
        # User growth over time (daily)
        user_growth = db.query(
            func.date(User.created_at).label('date'),
            func.count(User.id).label('count')
        ).filter(
            and_(
                User.created_at >= start_date,
                User.created_at <= end_date
            )
        ).group_by(func.date(User.created_at)).order_by('date').all()
        
        # User distribution by plan
        user_by_plan = db.query(
            Subscription.plan,
            func.count(func.distinct(Subscription.user_id))
        ).filter(
            Subscription.status == SubscriptionStatus.ACTIVE
        ).group_by(Subscription.plan).all()
        
        # User distribution by status
        user_by_status = db.query(
            User.is_active,
            User.is_verified,
            func.count(User.id)
        ).group_by(User.is_active, User.is_verified).all()
        
        # User retention (users who logged in in last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        # Note: This is a simplified calculation. In production, you'd track last_login_at
        active_recently = db.query(func.count(func.distinct(User.id))).filter(
            User.last_login_ip.isnot(None)  # Simplified: users who have logged in
        ).scalar() or 0
        
        total_verified = db.query(func.count(User.id)).filter(
            User.is_verified == True
        ).scalar() or 0
        
        retention_rate = 0
        if total_verified > 0:
            retention_rate = (active_recently / total_verified) * 100
        
        return {
            "user_growth": [
                {
                    "date": date.isoformat(),
                    "count": count
                }
                for date, count in user_growth
            ],
            "distribution_by_plan": {
                plan.value: count for plan, count in user_by_plan
            },
            "distribution_by_status": {
                "active_verified": sum(count for is_active, is_verified, count in user_by_status if is_active and is_verified),
                "active_unverified": sum(count for is_active, is_verified, count in user_by_status if is_active and not is_verified),
                "inactive": sum(count for is_active, is_verified, count in user_by_status if not is_active)
            },
            "retention_rate": round(retention_rate, 2)
        }
    
    @staticmethod
    def get_subscription_stats(db: Session) -> Dict[str, Any]:
        """
        Get subscription statistics.
        
        Returns:
            dict: Subscription statistics
        """
        # Subscriptions by status
        by_status = db.query(
            Subscription.status,
            func.count(Subscription.id)
        ).group_by(Subscription.status).all()
        
        # Subscriptions by plan
        by_plan = db.query(
            Subscription.plan,
            func.count(Subscription.id)
        ).group_by(Subscription.plan).all()
        
        # Subscriptions by billing period
        by_billing = db.query(
            Subscription.billing_period,
            func.count(Subscription.id)
        ).group_by(Subscription.billing_period).all()
        
        return {
            "by_status": {
                status.value: count for status, count in by_status
            },
            "by_plan": {
                plan.value: count for plan, count in by_plan
            },
            "by_billing_period": {
                billing.value: count for billing, count in by_billing
            }
        }
    
    @staticmethod
    def get_usage_stats(db: Session) -> Dict[str, Any]:
        """
        Get usage statistics.
        
        Returns:
            dict: Usage statistics
        """
        # Total keyword searches
        total_keyword_searches = db.query(func.count(KeywordSearch.id)).scalar() or 0
        
        # Active keyword searches
        active_keyword_searches = db.query(func.count(KeywordSearch.id)).filter(
            KeywordSearch.enabled == True
        ).scalar() or 0
        
        # Total opportunities
        total_opportunities = db.query(func.count(Opportunity.id)).scalar() or 0
        
        # Opportunities this month
        this_month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        opportunities_this_month = db.query(func.count(Opportunity.id)).filter(
            Opportunity.created_at >= this_month_start
        ).scalar() or 0
        
        # Average usage per user
        users_with_usage = db.query(func.count(func.distinct(UsageMetric.user_id))).scalar() or 0
        avg_usage_per_user = 0
        if users_with_usage > 0:
            total_usage = db.query(func.sum(UsageMetric.count)).scalar() or 0
            avg_usage_per_user = total_usage / users_with_usage
        
        # Top users by usage
        top_users = db.query(
            User.email,
            User.full_name,
            func.sum(UsageMetric.count).label('total_usage')
        ).join(
            UsageMetric, UsageMetric.user_id == User.id
        ).group_by(User.id, User.email, User.full_name).order_by(
            func.sum(UsageMetric.count).desc()
        ).limit(10).all()
        
        return {
            "keyword_searches": {
                "total": total_keyword_searches,
                "active": active_keyword_searches
            },
            "opportunities": {
                "total": total_opportunities,
                "this_month": opportunities_this_month
            },
            "average_usage_per_user": round(avg_usage_per_user, 2),
            "top_users": [
                {
                    "email": email,
                    "full_name": full_name,
                    "total_usage": int(total_usage)
                }
                for email, full_name, total_usage in top_users
            ]
        }

