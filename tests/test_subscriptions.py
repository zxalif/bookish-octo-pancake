"""
Basic Subscription Tests

Tests for subscription creation, limits, and usage tracking.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from models.user import User
from models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus
from services.subscription_service import SubscriptionService


def test_create_free_subscription(client: TestClient, db: Session, test_user: User):
    """Test automatic free subscription creation."""
    subscription = SubscriptionService.create_free_subscription(test_user.id, db)
    
    assert subscription is not None
    assert subscription.plan == SubscriptionPlan.FREE
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.user_id == test_user.id


def test_get_active_subscription(client: TestClient, db: Session, test_user: User):
    """Test getting active subscription."""
    # Create free subscription
    SubscriptionService.create_free_subscription(test_user.id, db)
    
    subscription = SubscriptionService.get_active_subscription(test_user.id, db)
    
    assert subscription is not None
    assert subscription.plan == SubscriptionPlan.FREE
    assert subscription.status == SubscriptionStatus.ACTIVE


def test_check_usage_limit_free_tier(client: TestClient, db: Session, test_user: User):
    """Test usage limit checking for free tier."""
    # Create free subscription
    SubscriptionService.create_free_subscription(test_user.id, db)
    
    # Check keyword searches limit (should be 10 for free tier)
    can_create, current, limit = SubscriptionService.check_usage_limit(
        test_user.id,
        "keyword_searches",
        db
    )
    
    assert can_create is True
    assert current == 0
    assert limit == 10


def test_check_usage_limit_opportunities(client: TestClient, db: Session, test_user: User):
    """Test opportunities usage limit checking."""
    # Create free subscription
    SubscriptionService.create_free_subscription(test_user.id, db)
    
    # Check opportunities limit (should be 500 for free tier)
    can_create, current, limit = SubscriptionService.check_usage_limit(
        test_user.id,
        "opportunities_per_month",
        db
    )
    
    assert can_create is True
    assert current == 0
    assert limit == 500


def test_subscription_expires_after_30_days(client: TestClient, db: Session, test_user: User):
    """Test that free subscription expires after 30 days."""
    from datetime import datetime, timedelta
    
    # Create free subscription
    subscription = SubscriptionService.create_free_subscription(test_user.id, db)
    
    # Manually expire it (simulate 31 days passing)
    subscription.current_period_end = datetime.utcnow() - timedelta(days=1)
    db.commit()
    
    # Get active subscription (should return None)
    active = SubscriptionService.get_active_subscription(test_user.id, db)
    
    assert active is None

