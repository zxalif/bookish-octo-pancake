"""
E2E Test Result Model

Stores results from end-to-end tests run via Playwright.
"""

from sqlalchemy import Column, String, DateTime, Text, JSON, Boolean, Index, Float
from datetime import datetime

from core.database import Base
from models.base import generate_uuid, TimestampMixin, format_utc_datetime


class E2ETestResult(Base, TimestampMixin):
    """
    E2E test result for tracking automated test runs.
    
    Tracks:
    - Test execution results
    - Individual test step results
    - Test user information (for cleanup)
    - Screenshots and error details
    
    Attributes:
        id: Unique test result identifier (UUID)
        test_run_id: Unique identifier for this test run
        status: Overall test status (passed, failed, error)
        test_user_email: Email of test user created during test
        test_user_id: ID of test user created during test
        duration_ms: Total test duration in milliseconds
        steps: JSON array of individual test step results
        error_message: Error message if test failed
        screenshot_url: URL to screenshot if test failed
        metadata: Additional metadata (browser, environment, etc.)
    """
    
    __tablename__ = "e2e_test_results"
    
    # Primary Key
    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    # Test Run Information
    test_run_id = Column(String(36), nullable=False, index=True)  # Unique ID for this test run
    status = Column(String(20), nullable=False, index=True)  # passed, failed, error, running
    triggered_by = Column(String(50), nullable=True)  # manual, scheduled, deployment
    
    # Test User Information (for cleanup)
    test_user_email = Column(String(255), nullable=True, index=True)
    test_user_id = Column(String(36), nullable=True, index=True)
    
    # Test Execution Details
    duration_ms = Column(Float, nullable=True)  # Test duration in milliseconds
    steps = Column(JSON, nullable=True)  # Array of step results: [{"step": "register", "status": "passed", "duration_ms": 1234}, ...]
    error_message = Column(Text, nullable=True)  # Error message if test failed
    screenshot_path = Column(String(500), nullable=True)  # Path to screenshot if test failed
    
    # Metadata
    test_metadata = Column(JSON, nullable=True)  # Browser, environment, version, etc.
    
    # Indexes
    __table_args__ = (
        Index('ix_e2e_test_results_status_created', 'status', 'created_at'),
        Index('ix_e2e_test_results_test_user_email', 'test_user_email'),
    )
    
    def __repr__(self):
        return f"<E2ETestResult(id={self.id}, test_run_id={self.test_run_id}, status={self.status})>"
    
    def to_dict(self):
        """Convert test result to dictionary."""
        return {
            "id": self.id,
            "test_run_id": self.test_run_id,
            "status": self.status,
            "triggered_by": self.triggered_by,
            "test_user_email": self.test_user_email,
            "test_user_id": self.test_user_id,
            "duration_ms": self.duration_ms,
            "steps": self.steps,
            "error_message": self.error_message,
            "screenshot_path": self.screenshot_path,
            "metadata": self.test_metadata,  # Keep "metadata" in API response for consistency
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }

