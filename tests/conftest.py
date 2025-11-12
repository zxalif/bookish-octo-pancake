"""
Pytest Configuration and Fixtures

Provides shared test fixtures for all tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import app
from core.database import Base, get_db
from models.user import User
from models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus
from services.auth_service import AuthService
from services.subscription_service import SubscriptionService
from core.security import get_password_hash

# Use in-memory SQLite for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db():
    """Create a fresh database for each test."""
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db: Session):
    """Create a test client with database override."""
    def override_get_db():
        try:
            yield db
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def test_user(db: Session) -> User:
    """Create a test user."""
    user = User(
        email="test@example.com",
        password_hash=get_password_hash("TestPassword123!"),
        full_name="Test User",
        is_active=True,
        is_verified=True  # Verified for most tests
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(scope="function")
def test_user_with_subscription(db: Session, test_user: User) -> User:
    """Create a test user with free subscription."""
    SubscriptionService.create_free_subscription(test_user.id, db)
    db.refresh(test_user)
    return test_user


@pytest.fixture(scope="function")
def auth_token(client: TestClient, db: Session, test_user: User) -> str:
    """Get authentication token for test user."""
    # Ensure user is verified
    test_user.is_verified = True
    db.commit()
    
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": test_user.email,
            "password": "TestPassword123!"
        }
    )
    
    assert response.status_code == 200
    return response.json()["access_token"]
