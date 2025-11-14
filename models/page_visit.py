"""
Page Visit Model

Tracks page visits for analytics purposes.
Captures IP address, referrer, user agent, and page information.
"""

from sqlalchemy import Column, String, Text, Index
from datetime import datetime

from core.database import Base
from models.base import generate_uuid, TimestampMixin


class PageVisit(Base, TimestampMixin):
    """
    Page visit tracking model for analytics.
    
    Tracks:
    - Landing page visits
    - Page views
    - Referrer sources
    - IP addresses
    - User agents
    - Page paths
    
    Attributes:
        id: Unique visit identifier (UUID)
        page_path: The page path that was visited (e.g., "/", "/pricing")
        ip_address: IP address from which the visit originated
        user_agent: Browser/user agent string
        referrer: HTTP referrer header (where the user came from)
        utm_source: UTM source parameter (if present)
        utm_medium: UTM medium parameter (if present)
        utm_campaign: UTM campaign parameter (if present)
        user_id: Optional user ID if user is logged in
        session_id: Optional session identifier for tracking user sessions
        created_at: Timestamp of the visit
    """
    
    __tablename__ = "page_visits"
    
    # Primary Key
    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    # Page Information
    page_path = Column(String(500), nullable=False, index=True)  # e.g., "/", "/pricing", "/blog"
    
    # Visitor Information
    ip_address = Column(String(45), nullable=True, index=True)  # IPv6 max length is 45 chars
    user_agent = Column(String(500), nullable=True)  # Browser/user agent
    referrer = Column(String(1000), nullable=True)  # HTTP referrer (where they came from)
    
    # UTM Parameters (for marketing campaigns)
    utm_source = Column(String(100), nullable=True, index=True)  # e.g., "google", "facebook"
    utm_medium = Column(String(100), nullable=True, index=True)  # e.g., "cpc", "email"
    utm_campaign = Column(String(100), nullable=True, index=True)  # Campaign name
    
    # Optional User Tracking
    user_id = Column(String(36), nullable=True, index=True)  # If user is logged in
    session_id = Column(String(100), nullable=True, index=True)  # Session identifier
    
    # Additional Metadata
    country = Column(String(2), nullable=True, index=True)  # ISO country code (if geolocation available)
    device_type = Column(String(50), nullable=True, index=True)  # mobile, desktop, tablet (if detected)
    
    # Indexes for common queries
    __table_args__ = (
        Index('ix_page_visits_created_at', 'created_at'),
        Index('ix_page_visits_page_path_created', 'page_path', 'created_at'),
        Index('ix_page_visits_user_id_created', 'user_id', 'created_at'),
    )
    
    def __repr__(self):
        return f"<PageVisit(id={self.id}, page_path={self.page_path}, ip_address={self.ip_address})>"
    
    def to_dict(self):
        """Convert page visit to dictionary."""
        return {
            "id": self.id,
            "page_path": self.page_path,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "referrer": self.referrer,
            "utm_source": self.utm_source,
            "utm_medium": self.utm_medium,
            "utm_campaign": self.utm_campaign,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "country": self.country,
            "device_type": self.device_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

