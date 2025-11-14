"""
Subscription Management Service

Handles subscription lifecycle management:
- Sync subscription status with Paddle
- Handle expired subscriptions
- Process subscription renewals
- Retry failed payments
- Update billing periods
- Refresh usage metrics
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from models.subscription import Subscription, SubscriptionStatus, SubscriptionPlan
from models.payment import Payment, PaymentStatus
from models.user import User
from models.usage_metric import UsageMetric
from models.keyword_search import KeywordSearch
from services.payment_service import PaymentService
from services.price_service import PriceService
from services.usage_service import UsageService
from models.price import BillingPeriod
from core.logger import get_logger
from core.config import get_settings

settings = get_settings()
logger = get_logger(__name__)


class SubscriptionManagementService:
    """Service for managing subscription lifecycle."""
    
    @staticmethod
    def sync_subscriptions_with_paddle(db: Session) -> Dict[str, Any]:
        """
        Sync subscription status with Paddle for all active subscriptions.
        
        This should be run periodically (e.g., hourly) to ensure our database
        is in sync with Paddle's subscription status.
        
        Args:
            db: Database session
            
        Returns:
            Dict with sync results
        """
        # Check if Paddle is enabled before syncing
        if not PaymentService.is_paddle_enabled():
            logger.warning("Paddle is disabled. Skipping subscription sync with Paddle.")
            return {
                "status": "skipped",
                "message": "Paddle payment gateway is disabled",
                "synced_count": 0
            }
        
        try:
            paddle = PaymentService.get_paddle_client()
            
            # Get all active subscriptions with Paddle subscription IDs
            subscriptions = db.query(Subscription).filter(
                and_(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.paddle_subscription_id.isnot(None)
                )
            ).all()
            
            synced_count = 0
            updated_count = 0
            errors = []
            
            for subscription in subscriptions:
                try:
                    # Fetch subscription from Paddle
                    paddle_sub = paddle.subscriptions.get(subscription.paddle_subscription_id)
                    
                    # Map Paddle status to our status
                    status_map = {
                        "active": SubscriptionStatus.ACTIVE,
                        "canceled": SubscriptionStatus.CANCELLED,
                        "past_due": SubscriptionStatus.PAST_DUE,
                        "trialing": SubscriptionStatus.TRIALING,
                        "paused": SubscriptionStatus.CANCELLED,  # Treat paused as cancelled
                    }
                    
                    paddle_status = getattr(paddle_sub, 'status', None)
                    # Convert enum to string if needed (Paddle SDK returns enum objects)
                    if paddle_status is not None:
                        # Handle both enum objects (with .value) and string values
                        try:
                            if hasattr(paddle_status, 'value'):
                                # Get the value attribute (could be string or another enum)
                                status_value = paddle_status.value
                                # If it's still an enum, get its value recursively
                                if hasattr(status_value, 'value'):
                                    paddle_status_str = str(status_value.value).lower()
                                else:
                                    paddle_status_str = str(status_value).lower()
                            else:
                                # It's already a string or other type
                                paddle_status_str = str(paddle_status).lower()
                            
                            # Clean up the string (remove any enum class prefixes)
                            paddle_status_str = paddle_status_str.replace('subscriptionstatus.', '').replace('status.', '')
                            
                            if paddle_status_str in status_map:
                                new_status = status_map[paddle_status_str]
                                if subscription.status != new_status:
                                    subscription.status = new_status
                                    updated_count += 1
                                    logger.info(
                                        f"Updated subscription {subscription.id} status: "
                                        f"{subscription.status.value} -> {new_status.value}"
                                    )
                        except Exception as e:
                            logger.warning(
                                f"Error processing paddle_status for subscription {subscription.id}: {str(e)}, "
                                f"paddle_status type: {type(paddle_status)}, value: {paddle_status}"
                            )
                    
                    # Update billing period dates if available
                    old_period_start = subscription.current_period_start
                    if hasattr(paddle_sub, 'current_billing_period'):
                        period = paddle_sub.current_billing_period
                        if period:
                            if hasattr(period, 'starts_at') and period.starts_at:
                                new_period_start = period.starts_at
                                
                                # Normalize datetimes to UTC-aware for comparison
                                # Database datetime is naive (UTC), Paddle datetime may be aware
                                if old_period_start:
                                    # Ensure old_period_start is timezone-aware (UTC)
                                    if old_period_start.tzinfo is None:
                                        old_period_start_aware = old_period_start.replace(tzinfo=timezone.utc)
                                    else:
                                        old_period_start_aware = old_period_start.astimezone(timezone.utc)
                                else:
                                    old_period_start_aware = None
                                
                                # Ensure new_period_start is timezone-aware (UTC)
                                if new_period_start.tzinfo is None:
                                    new_period_start_aware = new_period_start.replace(tzinfo=timezone.utc)
                                else:
                                    new_period_start_aware = new_period_start.astimezone(timezone.utc)
                                
                                # Check if billing period renewed (new period started)
                                if old_period_start_aware and new_period_start_aware > old_period_start_aware:
                                    # Billing period renewed - reset keyword search limits
                                    SubscriptionManagementService.reset_keyword_search_limits_on_renewal(
                                        subscription, db
                                    )
                                
                                # Store as naive datetime (database expects naive UTC)
                                subscription.current_period_start = new_period_start_aware.replace(tzinfo=None)
                            if hasattr(period, 'ends_at') and period.ends_at:
                                period_ends_at = period.ends_at
                                # Normalize to UTC-aware, then convert to naive for database
                                if period_ends_at.tzinfo is None:
                                    period_ends_at_aware = period_ends_at.replace(tzinfo=timezone.utc)
                                else:
                                    period_ends_at_aware = period_ends_at.astimezone(timezone.utc)
                                # Store as naive datetime (database expects naive UTC)
                                subscription.current_period_end = period_ends_at_aware.replace(tzinfo=None)
                                subscription.next_billing_date = period_ends_at_aware.replace(tzinfo=None)
                    
                    synced_count += 1
                    
                except Exception as e:
                    error_msg = f"Error syncing subscription {subscription.id}: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    errors.append(error_msg)
            
            db.commit()
            
            return {
                "status": "success",
                "synced_count": synced_count,
                "updated_count": updated_count,
                "errors": errors
            }
            
        except Exception as e:
            logger.error(f"Error in sync_subscriptions_with_paddle: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "synced_count": 0,
                "updated_count": 0,
                "errors": [str(e)]
            }
    
    @staticmethod
    def process_expired_subscriptions(db: Session) -> Dict[str, Any]:
        """
        Mark subscriptions as expired if their period has ended.
        
        This should be run daily to check for expired subscriptions.
        
        Args:
            db: Database session
            
        Returns:
            Dict with processing results
        """
        now = datetime.utcnow()
        
        # Find active subscriptions that have passed their period end
        expired_subscriptions = db.query(Subscription).filter(
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.current_period_end.isnot(None),
                Subscription.current_period_end < now,
                Subscription.plan != SubscriptionPlan.FREE  # Free plan handled separately
            )
        ).all()
        
        expired_count = 0
        for subscription in expired_subscriptions:
            # Check if subscription should be cancelled or expired
            if subscription.cancel_at_period_end:
                subscription.status = SubscriptionStatus.CANCELLED
                logger.info(f"Marked subscription {subscription.id} as cancelled (cancel_at_period_end=True)")
            else:
                # Check if there's a Paddle subscription - if so, Paddle handles renewal
                # If no Paddle subscription, mark as expired
                if not subscription.paddle_subscription_id:
                    subscription.status = SubscriptionStatus.EXPIRED
                    logger.info(f"Marked subscription {subscription.id} as expired (no Paddle subscription)")
                # If Paddle subscription exists, Paddle will handle renewal via webhook
                # We'll sync status in sync_subscriptions_with_paddle
            
            expired_count += 1
        
        db.commit()
        
        return {
            "status": "success",
            "expired_count": expired_count
        }
    
    @staticmethod
    def process_past_due_subscriptions(db: Session) -> Dict[str, Any]:
        """
        Handle past_due subscriptions - retry payment or mark as expired.
        
        This should be run daily to check for past_due subscriptions.
        
        Args:
            db: Database session
            
        Returns:
            Dict with processing results
        """
        # Find past_due subscriptions
        past_due_subscriptions = db.query(Subscription).filter(
            Subscription.status == SubscriptionStatus.PAST_DUE
        ).all()
        
        processed_count = 0
        retried_count = 0
        expired_count = 0
        
        for subscription in past_due_subscriptions:
            try:
                # If subscription has been past_due for more than 7 days, mark as expired
                if subscription.last_billing_date:
                    days_past_due = (datetime.utcnow() - subscription.last_billing_date).days
                    if days_past_due > 7:
                        subscription.status = SubscriptionStatus.EXPIRED
                        expired_count += 1
                        logger.info(
                            f"Marked subscription {subscription.id} as expired "
                            f"(past_due for {days_past_due} days)"
                        )
                        continue
                
                # If Paddle subscription exists, Paddle will retry payment automatically
                # We just need to sync status
                if subscription.paddle_subscription_id:
                    # Sync with Paddle to get latest status
                    try:
                        # Check if Paddle is enabled
                        if not PaymentService.is_paddle_enabled():
                            logger.warning(f"Paddle is disabled. Skipping sync for subscription {subscription.id}.")
                            continue
                        
                        paddle = PaymentService.get_paddle_client()
                        paddle_sub = paddle.subscriptions.get(subscription.paddle_subscription_id)
                        paddle_status = getattr(paddle_sub, 'status', None)
                        
                        # Convert enum to string if needed (Paddle SDK returns enum objects)
                        if paddle_status:
                            # Handle both enum objects (with .value) and string values
                            if hasattr(paddle_status, 'value'):
                                paddle_status_str = paddle_status.value
                            else:
                                paddle_status_str = str(paddle_status).lower()
                            
                            if paddle_status_str == "active":
                                subscription.status = SubscriptionStatus.ACTIVE
                                subscription.last_billing_status = "completed"
                                retried_count += 1
                                logger.info(f"Subscription {subscription.id} payment retried successfully")
                            elif paddle_status_str == "past_due":
                                # Still past_due, Paddle will retry
                                logger.info(f"Subscription {subscription.id} still past_due, Paddle will retry")
                            elif paddle_status_str == "canceled":
                                subscription.status = SubscriptionStatus.CANCELLED
                                logger.info(f"Subscription {subscription.id} cancelled by Paddle")
                    except Exception as e:
                        logger.warning(f"Error syncing past_due subscription {subscription.id}: {str(e)}")
                
                processed_count += 1
                
            except Exception as e:
                logger.error(f"Error processing past_due subscription {subscription.id}: {str(e)}", exc_info=True)
        
        db.commit()
        
        return {
            "status": "success",
            "processed_count": processed_count,
            "retried_count": retried_count,
            "expired_count": expired_count
        }
    
    @staticmethod
    def check_upcoming_renewals(db: Session, days_ahead: int = 3) -> Dict[str, Any]:
        """
        Check for subscriptions with upcoming renewals and send reminder emails.
        
        This should be run daily to notify users about upcoming renewals.
        
        Args:
            db: Database session
            days_ahead: Number of days ahead to check (default: 3)
            
        Returns:
            Dict with check results
        """
        now = datetime.utcnow()
        renewal_date = now + timedelta(days=days_ahead)
        
        # Find subscriptions with renewals in the next N days
        upcoming_renewals = db.query(Subscription).filter(
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.next_billing_date.isnot(None),
                Subscription.next_billing_date <= renewal_date,
                Subscription.next_billing_date > now
            )
        ).all()
        
        renewal_count = len(upcoming_renewals)
        
        # TODO: Send renewal reminder emails
        # This can be implemented later if needed
        
        return {
            "status": "success",
            "upcoming_renewals": renewal_count,
            "renewal_date_threshold": renewal_date.isoformat()
        }
    
    @staticmethod
    def reset_keyword_search_limits_on_renewal(
        subscription: Subscription,
        db: Session
    ) -> Dict[str, Any]:
        """
        Reset keyword search limits when subscription billing period renews.
        
        This is called when a subscription's billing period ends and renews.
        It:
        - Resets keyword_searches_created_per_month usage metric for new billing period
        - Permanently deletes soft-deleted searches from previous billing period
        - Ensures limits are based on billing cycle, not calendar month
        
        IMPORTANT: Active keyword searches from previous billing period are NOT deleted.
        They continue to count toward the concurrent limit (keyword_searches) in the new period.
        Only the creation limit (keyword_searches_created_per_month) is reset to 0.
        
        Args:
            subscription: Subscription that just renewed
            db: Database session
            
        Returns:
            Dict with reset results
        """
        if not subscription.current_period_start:
            return {
                "status": "skipped",
                "message": "Subscription has no current_period_start"
            }
        
        now = datetime.utcnow()
        current_period_start = subscription.current_period_start
        
        # Calculate previous billing period end (just before current period started)
        previous_period_end = current_period_start - timedelta(seconds=1)
        
        reset_count = 0
        deleted_count = 0
        
        try:
            # 1. Reset keyword_searches_created_per_month usage metric for new billing period
            # Delete old metric if it exists (shouldn't, but just in case)
            old_metric = db.query(UsageMetric).filter(
                and_(
                    UsageMetric.user_id == subscription.user_id,
                    UsageMetric.subscription_id == subscription.id,
                    UsageMetric.metric_type == "keyword_searches_created_per_month",
                    UsageMetric.period_start < current_period_start
                )
            ).first()
            
            if old_metric:
                db.delete(old_metric)
                reset_count += 1
            
            # Create new usage metric for current billing period
            # Calculate period end based on billing period
            # Use subscription's current_period_end if available, otherwise calculate
            if subscription.current_period_end:
                period_end = subscription.current_period_end
            elif subscription.billing_period == BillingPeriod.MONTHLY:
                # Monthly: add 1 month to period start
                # Handle month-end edge cases properly
                if current_period_start.month == 12:
                    period_end = current_period_start.replace(year=current_period_start.year + 1, month=1, day=1) - timedelta(seconds=1)
                else:
                    # Try to add 1 month, handling day overflow (e.g., Jan 31 -> Feb 28/29)
                    try:
                        period_end = current_period_start.replace(month=current_period_start.month + 1) - timedelta(seconds=1)
                    except ValueError:
                        # Day doesn't exist in next month (e.g., Jan 31 -> Feb), use last day of next month
                        next_month = current_period_start.replace(month=current_period_start.month + 1, day=1)
                        period_end = (next_month + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
            else:  # YEARLY
                # Yearly: add 1 year to period start
                try:
                    period_end = current_period_start.replace(year=current_period_start.year + 1) - timedelta(seconds=1)
                except ValueError:
                    # Handle leap year edge case (Feb 29)
                    period_end = current_period_start.replace(year=current_period_start.year + 1, month=2, day=28)
            
            # Check if metric for current period already exists
            existing_metric = db.query(UsageMetric).filter(
                and_(
                    UsageMetric.user_id == subscription.user_id,
                    UsageMetric.subscription_id == subscription.id,
                    UsageMetric.metric_type == "keyword_searches_created_per_month",
                    UsageMetric.period_start == current_period_start
                )
            ).first()
            
            if not existing_metric:
                new_metric = UsageMetric(
                    user_id=subscription.user_id,
                    subscription_id=subscription.id,
                    metric_type="keyword_searches_created_per_month",
                    count=0,
                    period_start=current_period_start,
                    period_end=period_end
                )
                db.add(new_metric)
                reset_count += 1
            
            # 2. Permanently delete soft-deleted searches from previous billing period
            # These searches no longer count toward the limit after renewal
            previous_period_deleted = db.query(KeywordSearch).filter(
                and_(
                    KeywordSearch.user_id == subscription.user_id,
                    KeywordSearch.deleted_at.isnot(None),  # type: ignore
                    KeywordSearch.deleted_at < current_period_start  # Deleted before current period
                )
            ).all()
            
            for search in previous_period_deleted:
                db.delete(search)
                deleted_count += 1
            
            db.commit()
            
            logger.info(
                f"Reset keyword search limits for subscription {subscription.id} "
                f"(user {subscription.user_id}): "
                f"reset {reset_count} metrics, deleted {deleted_count} old soft-deleted searches"
            )
            
            return {
                "status": "success",
                "reset_metrics": reset_count,
                "deleted_searches": deleted_count
            }
            
        except Exception as e:
            logger.error(
                f"Error resetting keyword search limits for subscription {subscription.id}: {str(e)}",
                exc_info=True
            )
            return {
                "status": "error",
                "message": str(e),
                "reset_metrics": reset_count,
                "deleted_searches": deleted_count
            }
    
    @staticmethod
    def refresh_usage_metrics(db: Session) -> Dict[str, Any]:
        """
        Refresh usage metrics for all active subscriptions.
        
        This job:
        - Creates usage metrics for current period if they don't exist
        - Cleans up expired usage metrics from previous periods
        - Ensures all active subscriptions have current usage metrics
        
        This should be run daily (e.g., at midnight) to ensure usage metrics are up to date.
        
        Args:
            db: Database session
            
        Returns:
            Dict with refresh results
        """
        now = datetime.utcnow()
        current_period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Get all active subscriptions
        active_subscriptions = db.query(Subscription).filter(
            Subscription.status == SubscriptionStatus.ACTIVE
        ).all()
        
        refreshed_count = 0
        created_count = 0
        cleaned_count = 0
        errors = []
        
        for subscription in active_subscriptions:
            try:
                # Clean up expired usage metrics from previous periods
                expired_metrics = db.query(UsageMetric).filter(
                    and_(
                        UsageMetric.subscription_id == subscription.id,
                        UsageMetric.period_start < current_period_start
                    )
                ).all()
                
                for metric in expired_metrics:
                    db.delete(metric)
                    cleaned_count += 1
                
                # Ensure current period usage metrics exist for all metric types
                metric_types = [
                    "opportunities_per_month",
                    "api_calls_per_month",
                    "keyword_searches_created_per_month"
                ]
                
                for metric_type in metric_types:
                    # Check if current period metric exists
                    existing_metric = db.query(UsageMetric).filter(
                        and_(
                            UsageMetric.user_id == subscription.user_id,
                            UsageMetric.subscription_id == subscription.id,
                            UsageMetric.metric_type == metric_type,
                            UsageMetric.period_start == current_period_start
                        )
                    ).first()
                    
                    if not existing_metric:
                        # Create new usage metric for current period
                        period_end = (current_period_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
                        usage_metric = UsageMetric(
                            user_id=subscription.user_id,
                            subscription_id=subscription.id,
                            metric_type=metric_type,
                            count=0,
                            period_start=current_period_start,
                            period_end=period_end
                        )
                        db.add(usage_metric)
                        created_count += 1
                
                refreshed_count += 1
                
            except Exception as e:
                error_msg = f"Error refreshing usage metrics for subscription {subscription.id}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                errors.append(error_msg)
        
        db.commit()
        
        return {
            "status": "success",
            "refreshed_count": refreshed_count,
            "created_count": created_count,
            "cleaned_count": cleaned_count,
            "errors": errors
        }
    
    @staticmethod
    def calculate_proration(
        current_plan: str,
        new_plan: str,
        current_billing_period: str,
        new_billing_period: str,
        current_period_start: datetime,
        current_period_end: datetime,
        db: Session
    ) -> Dict[str, Any]:
        """
        Calculate proration preview for subscription upgrade/downgrade.
        
        NOTE: This is for PREVIEW only. Actual proration is handled by Paddle
        when updating the subscription via their API.
        
        This method provides an estimate of what the proration will be:
        - Credit for unused time on current plan
        - Charge for new plan
        - Net amount to charge/refund
        
        Args:
            current_plan: Current subscription plan
            new_plan: New subscription plan
            current_billing_period: Current billing period (monthly/yearly)
            new_billing_period: New billing period (monthly/yearly)
            current_period_start: Start of current billing period
            current_period_end: End of current billing period
            db: Database session
            
        Returns:
            Dict with proration preview details
        """
        now = datetime.utcnow()
        
        # Get prices for both plans
        current_price = PriceService.get_price_by_plan_and_period(
            current_plan, current_billing_period, db
        )
        new_price = PriceService.get_price_by_plan_and_period(
            new_plan, new_billing_period, db
        )
        
        if not current_price or not new_price:
            return {
                "error": "Price not found for one or both plans",
                "proration_amount_cents": 0,
                "credit_amount_cents": 0,
                "charge_amount_cents": 0
            }
        
        # Calculate unused time in current period
        total_period_seconds = (current_period_end - current_period_start).total_seconds()
        used_seconds = (now - current_period_start).total_seconds()
        unused_seconds = max(0, total_period_seconds - used_seconds)
        unused_ratio = unused_seconds / total_period_seconds if total_period_seconds > 0 else 0
        
        # Calculate credit for unused time (in cents)
        current_amount_cents = current_price.amount
        credit_amount_cents = int(current_amount_cents * unused_ratio)
        
        # Calculate charge for new plan (prorated for remaining time)
        new_amount_cents = new_price.amount
        
        # For proration, we need to calculate the prorated amount for the new plan
        # based on remaining time in the current billing period
        # Paddle calculates this automatically, but we can estimate it here
        prorated_new_amount_cents = int(new_amount_cents * unused_ratio)
        
        # Calculate proration amount (prorated new plan - credit for old plan)
        # Positive = user pays more, Negative = user gets refund
        proration_amount_cents = prorated_new_amount_cents - credit_amount_cents
        
        return {
            "proration_amount_cents": proration_amount_cents,
            "credit_amount_cents": credit_amount_cents,
            "charge_amount_cents": prorated_new_amount_cents,
            "current_plan_amount_cents": current_amount_cents,
            "new_plan_amount_cents": new_amount_cents,
            "unused_ratio": unused_ratio,
            "unused_days": unused_seconds / 86400,  # Convert to days
            "formatted_proration": f"${proration_amount_cents / 100:.2f}",
            "formatted_credit": f"${credit_amount_cents / 100:.2f}",
            "formatted_charge": f"${prorated_new_amount_cents / 100:.2f}",
            "note": "This is an estimate. Paddle will calculate the exact proration amount."
        }
