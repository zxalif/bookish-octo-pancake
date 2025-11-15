"""
Lead Refresh Service

Handles refreshing leads from Rixly and sending email notifications to users.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
import asyncio

from models.user import User
from models.keyword_search import KeywordSearch
from models.opportunity import Opportunity, OpportunityStatus
from models.user_audit_log import UserAuditLog
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
        # IMPORTANT: Skip scheduled searches - webhook handles those to avoid duplicate emails
        # Only process one_time searches in scheduler refresh
        active_searches = db.query(KeywordSearch).filter(
            KeywordSearch.user_id == user.id,
            KeywordSearch.deleted_at.is_(None),  # type: ignore
            KeywordSearch.zola_search_id.isnot(None),  # type: ignore - zola_search_id stores Rixly search ID
            KeywordSearch.scraping_mode == "one_time"  # Only refresh one_time searches, webhook handles scheduled
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
                # Note: zola_search_id stores the Rixly search ID
                leads = await OpportunityService.fetch_leads_from_rixly(
                    rixly_search_id=search.zola_search_id,  # type: ignore - zola_search_id stores Rixly search ID
                    limit=100,
                    offset=0
                )
                
                if not leads:
                    continue
                
                # Extract source_post_ids from leads (batch check is more efficient)
                source_post_ids = []
                valid_leads = []
                
                for lead in leads:
                    source_post_id = lead.get("source_id") or lead.get("source_post_id") or lead.get("id", "")
                    if not source_post_id:
                        logger.warning(f"Skipping lead without source_id: {lead.get('title', 'Unknown')}")
                        continue
                    source_post_ids.append(source_post_id)
                    valid_leads.append((source_post_id, lead))
                
                # Batch check for existing opportunities (more efficient than checking one-by-one)
                # IMPORTANT: Check by user_id, not just keyword_search_id, because UniqueConstraint
                # is on (user_id, source_post_id) - same lead can't exist twice for same user
                from sqlalchemy.exc import IntegrityError
                
                existing_source_ids = set()
                if source_post_ids:
                    existing = db.query(Opportunity.source_post_id).filter(
                        Opportunity.user_id == user.id,
                        Opportunity.source_post_id.in_(source_post_ids)
                    ).all()
                    existing_source_ids = {row[0] for row in existing}
                    logger.info(
                        f"Found {len(existing_source_ids)} existing opportunities out of {len(source_post_ids)} leads"
                    )
                
                # Process new leads
                new_count = 0
                for source_post_id, lead in valid_leads:
                    if source_post_id in existing_source_ids:
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
                    except IntegrityError as e:
                        # Handle database constraint violation (duplicate)
                        db.rollback()
                        logger.warning(f"Duplicate opportunity detected (constraint violation): {source_post_id}")
                        continue
                    except Exception as e:
                        logger.error(f"Error converting lead to opportunity: {str(e)}")
                        db.rollback()
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
        # Use asyncio.to_thread to run blocking email sending in a thread pool (non-blocking)
        email_sent = False
        if send_email and total_new_opportunities > 0 and user.email_notifications_enabled:
            try:
                # Run email sending in thread pool to avoid blocking scheduler
                # This allows scheduler to continue processing other users while email is being sent
                await asyncio.to_thread(
                    LeadRefreshService._send_new_leads_email_sync,
                    user=user,
                    new_opportunities_count=total_new_opportunities,
                    searches_with_leads=searches_with_new_leads,
                    db=db
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
    def _send_new_leads_email_sync(
        user: User,
        new_opportunities_count: int,
        searches_with_leads: List[Dict[str, Any]],
        db: Session
    ) -> bool:
        """
        Synchronous version of send_new_leads_email for use in thread pool.
        
        This method sends the email and creates the audit log in a blocking manner,
        but is called via asyncio.to_thread() to avoid blocking the scheduler.
        """
        try:
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
            
            # Use leads@clienthunt.app as sender for consistency with webhook emails
            from_email = "leads@clienthunt.app"
            from_name = "ClientHunt Leads"
            
            # Send email (synchronous call)
            email_sent = EmailService._send_email(
                to_email=user.email,
                subject=subject,
                html_body=html_body,
                text_body=message,
                from_email=from_email,
                from_name=from_name
            )
            
            if email_sent:
                # Create audit log entry for email notification
                try:
                    searches_summary = ", ".join([f"{s['search_name']}: {s['new_count']}" for s in searches_with_leads])
                    audit_log = UserAuditLog(
                        user_id=user.id,
                        action="leads_notification_email_sent",
                        ip_address=None,  # Scheduler doesn't have IP
                        user_agent="Scheduler Service",
                        details=f"Lead notification email sent via scheduler: total_leads={new_opportunities_count}, searches={searches_summary}, scraping_mode=one_time"
                    )
                    db.add(audit_log)
                    db.commit()
                except Exception as e:
                    # Don't fail email sending if audit log fails
                    logger.warning(f"Failed to create audit log for leads notification email: {str(e)}")
            
            return email_sent
        except Exception as e:
            logger.error(f"Error in _send_new_leads_email_sync for user {user.id}: {str(e)}", exc_info=True)
            return False
    
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
        
        # Use leads@clienthunt.app as sender for consistency with webhook emails
        from_email = "leads@clienthunt.app"
        from_name = "ClientHunt Leads"
        
        return EmailService._send_email(
            to_email=user.email,
            subject=subject,
            html_body=html_body,
            text_body=message,
            from_email=from_email,
            from_name=from_name
        )

