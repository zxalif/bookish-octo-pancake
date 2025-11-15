"""
Analytics Routes

Handles page visit tracking and analytics endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import re
import httpx

from core.database import get_db
from api.middleware.rate_limit import limiter
from models.page_visit import PageVisit
from core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


def get_client_ip(request: Request) -> Optional[str]:
    """
    Get client IP address from request.
    
    Checks various headers for the real IP (behind proxy/load balancer).
    """
    # Check X-Forwarded-For header (most common for proxies)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, take the first one
        ip = forwarded_for.split(",")[0].strip()
        if ip:
            return ip
    
    # Check X-Real-IP header (Nginx)
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    # Fallback to direct client IP
    if request.client:
        return request.client.host
    
    return None


def detect_device_type(user_agent: Optional[str]) -> Optional[str]:
    """
    Simple device type detection from user agent.
    
    Returns: 'mobile', 'tablet', or 'desktop'
    """
    if not user_agent:
        return None
    
    user_agent_lower = user_agent.lower()
    
    # Mobile devices
    mobile_patterns = ['mobile', 'android', 'iphone', 'ipod', 'blackberry', 'windows phone']
    if any(pattern in user_agent_lower for pattern in mobile_patterns):
        # Check if it's a tablet
        tablet_patterns = ['ipad', 'tablet', 'playbook']
        if any(pattern in user_agent_lower for pattern in tablet_patterns):
            return 'tablet'
        return 'mobile'
    
    return 'desktop'


async def get_country_from_ip(ip_address: Optional[str]) -> Optional[str]:
    """
    Get country code (ISO 3166-1 alpha-2) from IP address using ip-api.com.
    
    Uses free tier of ip-api.com (no API key required, 45 requests/minute limit).
    Falls back gracefully if service is unavailable.
    
    Args:
        ip_address: IP address to geolocate
        
    Returns:
        ISO country code (2 letters, e.g., 'US', 'GB') or None if unavailable
    """
    if not ip_address:
        return None
    
    # Skip local/private IPs
    if ip_address in ['127.0.0.1', 'localhost', '::1'] or ip_address.startswith('192.168.') or ip_address.startswith('10.') or ip_address.startswith('172.'):
        return None
    
    try:
        # Use ip-api.com free tier (no API key required)
        # Rate limit: 45 requests/minute
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(
                f"http://ip-api.com/json/{ip_address}",
                params={"fields": "countryCode,status"}
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    country_code = data.get("countryCode")
                    if country_code and len(country_code) == 2:
                        return country_code.upper()
            
            logger.debug(f"Failed to get country for IP {ip_address}: {response.status_code}")
            return None
            
    except httpx.TimeoutException:
        logger.debug(f"Timeout getting country for IP {ip_address}")
        return None
    except Exception as e:
        # Don't log errors for geolocation failures - it's not critical
        logger.debug(f"Error getting country for IP {ip_address}: {str(e)}")
        return None


# Request Models
class TrackPageVisitRequest(BaseModel):
    """Request model for tracking a page visit."""
    page_path: str = Field(..., max_length=500, description="Page path (e.g., '/', '/pricing')")
    referrer: Optional[str] = Field(None, max_length=1000, description="HTTP referrer")
    utm_source: Optional[str] = Field(None, max_length=100, description="UTM source parameter")
    utm_medium: Optional[str] = Field(None, max_length=100, description="UTM medium parameter")
    utm_campaign: Optional[str] = Field(None, max_length=100, description="UTM campaign parameter")
    session_id: Optional[str] = Field(None, max_length=100, description="Session identifier")


@router.post("/track-visit")
@limiter.limit("30/minute")  # Rate limit: 30 visits per minute per IP
async def track_page_visit(
    request: Request,
    visit_data: TrackPageVisitRequest,
    db: Session = Depends(get_db)
):
    """
    Track a page visit.
    
    This endpoint tracks page visits for analytics purposes.
    It captures:
    - Page path
    - IP address
    - User agent
    - Referrer
    - UTM parameters
    - Device type
    
    **Rate Limited**: 30 requests per minute per IP address
    
    **Privacy**: IP addresses are stored but can be anonymized if needed.
    No personal information is collected unless user is logged in.
    
    **Response 200**: Visit tracked successfully
    **Response 429**: Rate limit exceeded
    """
    try:
        # Get client IP
        ip_address = get_client_ip(request)
        
        # Get user agent
        user_agent = request.headers.get("User-Agent")
        
        # Sanitize page_path (prevent XSS/injection)
        page_path = visit_data.page_path[:500].strip()
        # Only allow valid URL paths
        if not re.match(r'^[/][a-zA-Z0-9\-_/\.]*$', page_path):
            page_path = "/"  # Default to home if invalid
        
        # Sanitize referrer
        referrer = None
        if visit_data.referrer:
            referrer = visit_data.referrer[:1000].strip()
            # Basic URL validation
            if not (referrer.startswith("http://") or referrer.startswith("https://")):
                referrer = None
        
        # Sanitize UTM parameters
        utm_source = None
        if visit_data.utm_source:
            utm_source = re.sub(r'[^a-zA-Z0-9\-_]', '', visit_data.utm_source[:100])
        
        utm_medium = None
        if visit_data.utm_medium:
            utm_medium = re.sub(r'[^a-zA-Z0-9\-_]', '', visit_data.utm_medium[:100])
        
        utm_campaign = None
        if visit_data.utm_campaign:
            utm_campaign = re.sub(r'[^a-zA-Z0-9\-_]', '', visit_data.utm_campaign[:100])
        
        # Detect device type
        device_type = detect_device_type(user_agent)
        
        # Get country from IP address (non-blocking, fails gracefully)
        country = await get_country_from_ip(ip_address)
        
        # Create page visit record
        page_visit = PageVisit(
            page_path=page_path,
            ip_address=ip_address,
            user_agent=user_agent[:500] if user_agent else None,
            referrer=referrer,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            session_id=visit_data.session_id[:100] if visit_data.session_id else None,
            device_type=device_type,
            country=country,
        )
        
        db.add(page_visit)
        db.commit()
        
        logger.debug(
            f"Page visit tracked: {page_path} from {ip_address}",
            extra={
                "page_path": page_path,
                "ip_address": ip_address,
                "referrer": referrer,
                "utm_source": utm_source,
                "device_type": device_type,
            }
        )
        
        return {
            "success": True,
            "message": "Visit tracked successfully",
            "visit_id": page_visit.id
        }
        
    except Exception as e:
        logger.error(f"Error tracking page visit: {str(e)}", exc_info=True)
        db.rollback()
        # Don't expose internal errors to client
        return {
            "success": False,
            "message": "Failed to track visit"
        }


@router.get("/visits/stats")
@limiter.limit("60/minute")
async def get_visit_stats(
    request: Request,
    page_path: Optional[str] = Query(None, description="Filter by page path"),
    start_date: Optional[str] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="End date (ISO format)"),
    db: Session = Depends(get_db)
):
    """
    Get visit statistics.
    
    Returns aggregated visit statistics.
    
    **Rate Limited**: 60 requests per minute
    
    **Response 200**: Visit statistics
    """
    try:
        query = db.query(PageVisit)
        
        # Apply filters
        if page_path:
            query = query.filter(PageVisit.page_path == page_path)
        
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
        
        # Get unique IPs (approximate)
        unique_ips = db.query(PageVisit.ip_address).distinct().count()
        
        # Get top referrers
        from sqlalchemy import func
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
        
        # Get top pages
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
        
        return {
            "total_visits": total_visits,
            "unique_ips": unique_ips,
            "top_referrers": [{"referrer": ref, "count": count} for ref, count in top_referrers],
            "top_pages": [{"page_path": path, "count": count} for path, count in top_pages],
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting visit stats: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get visit statistics"
        )

