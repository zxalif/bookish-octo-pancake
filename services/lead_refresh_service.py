"""
Lead Refresh Service

Handles refreshing leads from Rixly and sending email notifications to users.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from models.user import User
from models.keyword_search import KeywordSearch
from models.opportunity import Opportunity, OpportunityStatus
from services.opportunity_service import OpportunityService
from services.email_service import EmailService
from core.logger import get_logger

logger = get_logger(__name__)


class LeadRefreshService:
    """Service for refreshing leads from Rixly and sending notifications."""
    
    @staticmethod
    async def refresh_leads_for_user(
        db: Session,
        user: User,
        send_email: bool = True
    ) -> Dict[str, Any]:
        """
        Refresh leads from Rixly for all active keyword searches of a user.
        
        Args:
            db: Database session
            user: User to refresh leads for
            send_email: Whether to send email notification if new leads found
            
        Returns:
            dict: Summary of refresh operation
        """
        # Get all active keyword searches for user
        active_searches = db.query(KeywordSearch).filter(
            KeywordSearch.user_id == user.id,
            KeywordSearch.deleted_at.is_(None),  # type: ignore
            KeywordSearch.rixly_search_id.isnot(None)  # type: ignore
        ).all()
        
        if not active_searches:
            return {
                "user_id": user.id,
                "searches_checked": 0,
                "new_opportunities": 0,
                "email_sent": False,
                "message": "No active searches with Rixly integration found"
            }
        
        total_new_opportunities = 0
        searches_with_new_leads = []
        
        for search in active_searches:
            try:
                # Fetch latest leads from Rixly
                leads = await OpportunityService.fetch_leads_from_rixly(
                    rixly_search_id=search.rixly_search_id,  # type: ignore
                    limit=100,
                    offset=0
                )
                
                if not leads:
                    continue
                
                # Get existing opportunity Rixly IDs to avoid duplicates
                existing_rixly_ids = {
                    opp.rixly_lead_id  # type: ignore
                    for opp in db.query(Opportunity).filter(
                        Opportunity.keyword_search_id == search.id,
                        Opportunity.rixly_lead_id.isnot(None)  # type: ignore
                    ).all()
                    if opp.rixly_lead_id  # type: ignore
                }
                
                # Process new leads
                new_count = 0
                for lead in leads:
                    rixly_lead_id = lead.get("id") or lead.get("rixly_lead_id")
                    
                    if not rixly_lead_id or rixly_lead_id in existing_rixly_ids:
                        continue
                    
                    # Convert lead to opportunity
                    try:
                        opportunity = OpportunityService.convert_zola_lead_to_opportunity(
                            zola_lead=lead,
                            user_id=user.id,
                            keyword_search_id=search.id
                        )
                        db.add(opportunity)
                        new_count += 1
                        total_new_opportunities += 1
                    except Exception as e:
                        logger.error(f"Error converting lead to opportunity: {str(e)}")
                        continue
                
                if new_count > 0:
                    searches_with_new_leads.append({
                        "search_id": search.id,
                        "search_name": search.name,
                        "new_count": new_count
                    })
                    db.commit()
                    
            except Exception as e:
                logger.error(f"Error refreshing leads for search {search.id}: {str(e)}")
                db.rollback()
                continue
        
        # Send email notification if new leads found and user has notifications enabled
        email_sent = False
        if send_email and total_new_opportunities > 0 and user.email_notifications_enabled:
            try:
                await LeadRefreshService.send_new_leads_email(
                    user=user,
                    new_opportunities_count=total_new_opportunities,
                    searches_with_leads=searches_with_new_leads
                )
                email_sent = True
            except Exception as e:
                logger.error(f"Error sending email notification to user {user.id}: {str(e)}")
        
        return {
            "user_id": user.id,
            "searches_checked": len(active_searches),
            "new_opportunities": total_new_opportunities,
            "searches_with_new_leads": searches_with_new_leads,
            "email_sent": email_sent,
            "message": f"Refreshed {len(active_searches)} searches, found {total_new_opportunities} new opportunities"
        }
    
    @staticmethod
    async def refresh_leads_for_all_users(db: Session) -> Dict[str, Any]:
        """
        Refresh leads for all users with active subscriptions and active searches.
        
        Args:
            db: Database session
            
        Returns:
            dict: Summary of refresh operation
        """
        # Get all active users with active subscriptions
        from models.subscription import Subscription
        
        active_users = db.query(User).join(Subscription).filter(
            User.is_active == True,  # type: ignore
            User.email_notifications_enabled == True,  # Only users who want notifications
            Subscription.status.in_(["active", "trialing"])  # type: ignore
        ).distinct().all()
        
        total_users = len(active_users)
        total_new_opportunities = 0
        users_notified = 0
        
        for user in active_users:
            try:
                result = await LeadRefreshService.refresh_leads_for_user(
                    db=db,
                    user=user,
                    send_email=True
                )
                total_new_opportunities += result["new_opportunities"]
                if result["email_sent"]:
                    users_notified += 1
            except Exception as e:
                logger.error(f"Error refreshing leads for user {user.id}: {str(e)}")
                continue
        
        return {
            "users_processed": total_users,
            "users_notified": users_notified,
            "total_new_opportunities": total_new_opportunities,
            "message": f"Processed {total_users} users, found {total_new_opportunities} new opportunities, sent {users_notified} emails"
        }
    
    @staticmethod
    async def send_new_leads_email(
        user: User,
        new_opportunities_count: int,
        searches_with_leads: List[Dict[str, Any]]
    ) -> bool:
        """
        Send email notification to user about new leads.
        
        Args:
            user: User to send email to
            new_opportunities_count: Number of new opportunities found
            searches_with_leads: List of searches with new leads
            
        Returns:
            bool: True if email was sent successfully
        """
        from services.email_service import EmailService
        
        # Build email content
        subject = f"🎯 {new_opportunities_count} New Lead{'s' if new_opportunities_count > 1 else ''} Found!"
        
        searches_list = "\n".join([
            f"  • {s['search_name']}: {s['new_count']} new lead{'s' if s['new_count'] > 1 else ''}"
            for s in searches_with_leads
        ])
        
        message = f"""
Hi {user.full_name},

Great news! We found {new_opportunities_count} new lead{'s' if new_opportunities_count > 1 else ''} matching your keyword searches:

{searches_list}

Log in to your dashboard to view and contact these leads:
https://clienthunt.app/dashboard/opportunities

Happy hunting!

Best regards,
The ClientHunt Team
        """.strip()
        
        # Create HTML version of email
        html_body = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #2563eb;">🎯 {new_opportunities_count} New Lead{'s' if new_opportunities_count > 1 else ''} Found!</h2>
            <p>Hi {user.full_name},</p>
            <p>Great news! We found {new_opportunities_count} new lead{'s' if new_opportunities_count > 1 else ''} matching your keyword searches:</p>
            <ul style="list-style-type: none; padding-left: 0;">
              {''.join([f'<li style="margin: 10px 0;">• <strong>{s["search_name"]}</strong>: {s["new_count"]} new lead{"s" if s["new_count"] > 1 else ""}</li>' for s in searches_with_leads])}
            </ul>
            <p style="margin: 30px 0;">
              <a href="https://clienthunt.app/dashboard/opportunities" style="background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">View Leads in Dashboard</a>
            </p>
            <p>Happy hunting!</p>
            <p>Best regards,<br>The ClientHunt Team</p>
          </body>
        </html>
        """
        
        return EmailService._send_email(
            to_email=user.email,
            subject=subject,
            html_body=html_body,
            text_body=message
        )

