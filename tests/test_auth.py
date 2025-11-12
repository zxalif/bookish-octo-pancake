"""
Basic Authentication Tests

Tests for user registration, login, and email verification.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from models.user import User
from services.auth_service import AuthService


def test_user_registration(client: TestClient, db: Session):
    """Test user registration endpoint."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "TestPassword123!",
            "full_name": "Test User"
        }
    )
    
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert "user" in data
    assert data["user"]["email"] == "test@example.com"
    assert data["user"]["is_verified"] is False


def test_user_login(client: TestClient, db: Session, test_user: User):
    """Test user login endpoint."""
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": test_user.email,
            "password": "TestPassword123!"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "user" in data
    assert data["user"]["email"] == test_user.email


def test_user_login_invalid_credentials(client: TestClient, db: Session, test_user: User):
    """Test login with invalid credentials."""
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": test_user.email,
            "password": "WrongPassword"
        }
    )
    
    assert response.status_code == 401


def test_user_login_unverified_email(client: TestClient, db: Session, test_user: User):
    """Test login with unverified email should fail."""
    # Ensure user is not verified
    test_user.is_verified = False
    db.commit()
    
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": test_user.email,
            "password": "TestPassword123!"
        }
    )
    
    assert response.status_code == 403
    assert "verified" in response.json()["detail"].lower()


def test_get_current_user(client: TestClient, db: Session, test_user: User, auth_token: str):
    """Test getting current user with valid token."""
    response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {auth_token}"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == test_user.email
    assert data["id"] == test_user.id


def test_get_current_user_invalid_token(client: TestClient):
    """Test getting current user with invalid token."""
    response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer invalid_token"}
    )
    
    assert response.status_code == 401

