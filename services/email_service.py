"""
Email Service

Handles email sending for:
- Password reset
- Email verification
- Notifications
- Subscription updates
"""

from typing import Optional
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta
import secrets
import hashlib
import io
from sqlalchemy.orm import Session

# Optional imports for PDF generation
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

from core.config import get_settings
from core.logger import get_logger
from models.user import User
# Note: AuthService imported locally in send_password_reset_email to avoid circular import

settings = get_settings()
logger = get_logger(__name__)


class EmailService:
    """Service for sending emails."""
    
    # Retry configuration
    MAX_RETRIES = 3
    INITIAL_RETRY_DELAY = 1  # seconds
    MAX_RETRY_DELAY = 8  # seconds
    
    @staticmethod
    def _is_retryable_error(error: Exception) -> bool:
        """
        Determine if an SMTP error is retryable (temporary failure).
        
        Retryable errors:
        - Temporary authentication failures (error code 454)
        - Connection errors (network issues)
        - Timeout errors
        - Temporary SMTP errors (4xx codes that are temporary)
        
        Non-retryable errors:
        - Permanent authentication failures (wrong credentials)
        - Invalid email addresses
        - Permanent SMTP errors (5xx codes that are permanent)
        
        Args:
            error: Exception to check
            
        Returns:
            bool: True if error is retryable, False otherwise
        """
        # Temporary authentication failures (e.g., error 454)
        if isinstance(error, smtplib.SMTPAuthenticationError):
            # Error code 454 indicates temporary authentication failure
            # Error code 535 indicates permanent authentication failure (wrong credentials)
            if hasattr(error, 'smtp_code'):
                if error.smtp_code == 454:
                    return True  # Temporary failure - retry
                elif error.smtp_code == 535:
                    return False  # Permanent failure - don't retry
            # Check error message for temporary indicators
            error_msg = str(error).lower()
            if 'temporary' in error_msg or 'connection lost' in error_msg:
                return True
            return False  # Default: don't retry authentication errors
        
        # Connection errors are usually temporary (network issues)
        if isinstance(error, smtplib.SMTPConnectError):
            return True
        
        # Timeout errors are temporary
        if isinstance(error, (smtplib.SMTPServerDisconnected, TimeoutError)):
            return True
        
        # Other SMTP exceptions - check if they're temporary
        if isinstance(error, smtplib.SMTPException):
            error_msg = str(error).lower()
            # Temporary errors often contain these keywords
            if any(keyword in error_msg for keyword in ['temporary', 'timeout', 'connection', 'network']):
                return True
            return False
        
        # For other exceptions, don't retry (likely programming errors)
        return False
    
    @staticmethod
    def _create_smtp_connection():
        """
        Create SMTP connection.
        
        Supports both STARTTLS (port 587) and SSL (port 465) connections.
        Handles production environment differences.
        
        Returns:
            SMTP connection object
            
        Raises:
            ValueError: If SMTP configuration is incomplete
            smtplib.SMTPException: If connection or authentication fails
        """
        if not settings.SMTP_HOST or not settings.SMTP_USER or not settings.SMTP_PASSWORD:
            raise ValueError("SMTP configuration is incomplete. Check SMTP_HOST, SMTP_USER, and SMTP_PASSWORD.")
        
        host = settings.SMTP_HOST
        port = settings.SMTP_PORT
        user = settings.SMTP_USER
        password = settings.SMTP_PASSWORD
        
        logger.info(f"Connecting to SMTP server: {host}:{port} as {user}")
        
        try:
            # Use SSL for port 465, STARTTLS for port 587
            if port == 465:
                logger.debug("Using SSL connection (port 465)")
                server = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                logger.debug("Using STARTTLS connection (port 587 or other)")
                server = smtplib.SMTP(host, port, timeout=30)
                # Enable debug for troubleshooting (comment out in production if too verbose)
                # server.set_debuglevel(1)
                server.starttls()
            
            logger.debug("SMTP connection established, attempting login...")
            server.login(user, password)
            logger.info("SMTP authentication successful")
            
            return server
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(
                f"SMTP authentication failed for {user} at {host}:{port}. "
                f"Error code: {e.smtp_code}, Error: {e.smtp_error}. "
                f"Possible causes: 1) Wrong credentials, 2) IP not whitelisted, "
                f"3) Account requires app password, 4) Account locked/suspended"
            )
            raise
        except smtplib.SMTPConnectError as e:
            logger.error(
                f"Failed to connect to SMTP server {host}:{port}. "
                f"Error: {e}. Check firewall/network settings."
            )
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error connecting to SMTP {host}:{port}: {type(e).__name__}: {e}",
                exc_info=True
            )
            raise
    
    @staticmethod
    def _send_email(
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = None
    ) -> bool:
        """
        Send email via SMTP with retry logic for temporary failures.
        
        Implements exponential backoff retry for temporary SMTP errors:
        - Retries up to MAX_RETRIES times (default: 3)
        - Uses exponential backoff: 1s, 2s, 4s (capped at MAX_RETRY_DELAY)
        - Only retries temporary errors (connection issues, temporary auth failures)
        - Does not retry permanent errors (wrong credentials, invalid email)
        
        Args:
            to_email: Recipient email address
            subject: Email subject
            html_body: HTML email body
            text_body: Plain text email body (optional)
            from_email: Sender email address (optional, defaults to SMTP_FROM_EMAIL)
            from_name: Sender name (optional, defaults to SMTP_FROM_NAME)
            
        Returns:
            bool: True if sent successfully, False otherwise
        """
        # Use provided from_email/from_name or fall back to defaults
        sender_email = from_email or settings.SMTP_FROM_EMAIL
        sender_name = from_name or settings.SMTP_FROM_NAME
        
        # Create message once (reused for retries)
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{sender_name} <{sender_email}>"
        msg['To'] = to_email
        
        # Add text and HTML parts
        if text_body:
            text_part = MIMEText(text_body, 'plain')
            msg.attach(text_part)
        
        html_part = MIMEText(html_body, 'html')
        msg.attach(html_part)
        
        # Retry loop with exponential backoff
        last_error = None
        for attempt in range(EmailService.MAX_RETRIES):
            try:
                # Send email
                server = EmailService._create_smtp_connection()
                try:
                    server.send_message(msg)
                    if attempt > 0:
                        logger.info(
                            f"Email sent successfully to {to_email} on attempt {attempt + 1} "
                            f"(subject: {subject})"
                        )
                    else:
                        logger.info(f"Email sent successfully to {to_email} (subject: {subject})")
                    return True
                finally:
                    try:
                        server.quit()
                    except Exception:
                        pass  # Ignore errors when closing connection
                        
            except Exception as e:
                last_error = e
                
                # Check if error is retryable
                if not EmailService._is_retryable_error(e):
                    # Permanent error - don't retry
                    if isinstance(e, smtplib.SMTPAuthenticationError):
                        logger.error(
                            f"SMTP authentication failed when sending to {to_email} (permanent failure). "
                            f"This might be due to: 1) Wrong credentials, 2) IP restrictions on email provider, "
                            f"3) Account security settings, 4) Need to whitelist VPS IP in email provider settings. "
                            f"Error code: {getattr(e, 'smtp_code', 'unknown')}, "
                            f"Error: {getattr(e, 'smtp_error', str(e))}"
                        )
                    else:
                        logger.error(
                            f"Permanent SMTP error when sending to {to_email}: {type(e).__name__}: {str(e)}",
                            exc_info=True
                        )
                    return False
                
                # Temporary error - retry if attempts remaining
                if attempt < EmailService.MAX_RETRIES - 1:
                    # Calculate exponential backoff delay
                    delay = min(
                        EmailService.INITIAL_RETRY_DELAY * (2 ** attempt),
                        EmailService.MAX_RETRY_DELAY
                    )
                    
                    error_info = ""
                    if isinstance(e, smtplib.SMTPAuthenticationError):
                        error_info = f" (error code: {getattr(e, 'smtp_code', 'unknown')})"
                    elif isinstance(e, smtplib.SMTPConnectError):
                        error_info = " (connection error)"
                    
                    logger.warning(
                        f"Temporary SMTP error when sending to {to_email} (attempt {attempt + 1}/{EmailService.MAX_RETRIES})"
                        f"{error_info}: {type(e).__name__}: {str(e)}. "
                        f"Retrying in {delay}s..."
                    )
                    
                    time.sleep(delay)
                else:
                    # Last attempt failed
                    if isinstance(e, smtplib.SMTPAuthenticationError):
                        logger.error(
                            f"SMTP authentication failed when sending to {to_email} after {EmailService.MAX_RETRIES} attempts. "
                            f"This might be due to: 1) IP restrictions on email provider (PrivateEmail may block VPS IPs), "
                            f"2) Different network in production vs localhost, 3) Account security settings, "
                            f"4) Need to whitelist VPS IP in email provider settings. "
                            f"Error code: {getattr(e, 'smtp_code', 'unknown')}, "
                            f"Error: {getattr(e, 'smtp_error', str(e))}"
                        )
                    else:
                        logger.error(
                            f"Failed to send email to {to_email} after {EmailService.MAX_RETRIES} attempts: "
                            f"{type(e).__name__}: {str(e)}",
                            exc_info=True
                        )
        
        # All retries exhausted
        return False
    
    @staticmethod
    async def send_verification_email(email: str, user_id: str, token: str) -> bool:
        """
        Send email verification link.
        
        Args:
            email: User's email address
            user_id: User UUID
            token: Verification token (generated by AuthService)
            
        Returns:
            bool: True if sent successfully
        """
        verification_url = f"{settings.FRONTEND_URL}/verify-email?token={token}&user_id={user_id}"
        
        subject = "Verify Your ClientHunt Account"
        logo_url = f"{settings.FRONTEND_URL}/logo.png"
        html_body = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: #f9fafb;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; background-color: #ffffff;">
              <!-- Logo Header -->
              <div style="text-align: center; margin-bottom: 30px; padding: 20px 0;">
                <img src="{logo_url}" alt="ClientHunt Logo" style="max-width: 200px; height: auto;" />
              </div>
              
              <h2 style="color: #2563eb; margin-top: 0;">Welcome to ClientHunt!</h2>
              <p>Thank you for signing up! Please verify your email address to complete your registration.</p>
              <p style="margin: 30px 0;">
                <a href="{verification_url}" style="background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block; font-weight: 600;">Verify Email Address</a>
              </p>
              <p style="color: #6b7280; font-size: 14px;">Or copy and paste this URL into your browser:</p>
              <p style="background-color: #f3f4f6; padding: 10px; border-radius: 4px; word-break: break-all; font-size: 12px; color: #6b7280; margin: 10px 0;">{verification_url}</p>
              <p style="color: #6b7280; font-size: 14px;">This link will expire in 24 hours.</p>
              <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">If you didn't create an account, please ignore this email.</p>
              
              <!-- Footer -->
              <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e7eb; text-align: center; color: #9ca3af; font-size: 12px;">
                <p>© {datetime.now().year} ClientHunt. All rights reserved.</p>
              </div>
            </div>
          </body>
        </html>
        """
        text_body = f"""
        Welcome to ClientHunt!
        
        Thank you for signing up! Please verify your email address to complete your registration.
        
        Verify your email by visiting:
        {verification_url}
        
        This link will expire in 24 hours.
        
        If you didn't create an account, please ignore this email.
        """
        
        # Send from noreply@
        return EmailService._send_email(
            email, 
            subject, 
            html_body, 
            text_body,
            from_email=settings.SMTP_NOREPLY_EMAIL,
            from_name=settings.SMTP_FROM_NAME
        )
    
    @staticmethod
    async def send_password_reset_email(email: str, db: Session, token: Optional[str] = None) -> bool:
        """
        Send password reset email.
        
        Args:
            email: User's email address
            db: Database session
            token: Password reset token (if None, will be generated)
            
        Returns:
            bool: True if sent successfully
        """
        user = db.query(User).filter(User.email == email).first()
        if not user:
            # Don't reveal if email exists (security best practice)
            return True
        
        # Generate reset token if not provided
        if not token:
            # Import locally to avoid circular import with auth_service
            from services.auth_service import AuthService
            token = AuthService.generate_password_reset_token(user.id)
        
        # Include user_id in URL for better UX (frontend can pre-fill if needed)
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}&user_id={user.id}"
        
        subject = "Reset Your ClientHunt Password"
        logo_url = f"{settings.FRONTEND_URL}/logo.png"
        html_body = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: #f9fafb;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; background-color: #ffffff;">
              <!-- Logo Header -->
              <div style="text-align: center; margin-bottom: 30px; padding: 20px 0;">
                <img src="{logo_url}" alt="ClientHunt Logo" style="max-width: 200px; height: auto;" />
              </div>
              
              <h2 style="color: #2563eb; margin-top: 0;">Password Reset Request</h2>
              <p>You requested to reset your password. Click the link below to reset it:</p>
              <p style="margin: 30px 0;">
                <a href="{reset_url}" style="background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block; font-weight: 600;">Reset Password</a>
              </p>
              <p style="color: #6b7280; font-size: 14px;">Or copy and paste this URL into your browser:</p>
              <p style="background-color: #f3f4f6; padding: 10px; border-radius: 4px; word-break: break-all; font-size: 12px; color: #6b7280; margin: 10px 0;">{reset_url}</p>
              <p style="color: #6b7280; font-size: 14px;">This link will expire in 1 hour.</p>
              <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">If you didn't request a password reset, please ignore this email.</p>
              
              <!-- Footer -->
              <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e7eb; text-align: center; color: #9ca3af; font-size: 12px;">
                <p>© {datetime.now().year} ClientHunt. All rights reserved.</p>
              </div>
            </div>
          </body>
        </html>
        """
        text_body = f"""
        Password Reset Request
        
        You requested to reset your password. Visit this link to reset it:
        {reset_url}
        
        This link will expire in 1 hour.
        
        If you didn't request a password reset, please ignore this email.
        """
        
        return EmailService._send_email(email, subject, html_body, text_body)
    
    @staticmethod
    async def send_subscription_activated_email(
        user_id: str,
        plan_id: str,
        db: Session
    ) -> bool:
        """
        Send subscription activation email.
        
        Args:
            user_id: User UUID
            plan_id: Subscription plan ID
            db: Database session
            
        Returns:
            bool: True if sent successfully
        """
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return False
        
        plan_names = {
            "starter": "Starter",
            "professional": "Professional",
            "power": "Power"
        }
        plan_name = plan_names.get(plan_id, plan_id)
        
        subject = f"Welcome to ClientHunt {plan_name} Plan!"
        html_body = f"""
        <html>
          <body>
            <h2>Subscription Activated</h2>
            <p>Your {plan_name} subscription has been activated!</p>
            <p>You now have access to all features of the {plan_name} plan.</p>
            <p>Thank you for choosing ClientHunt!</p>
          </body>
        </html>
        """
        text_body = f"""
        Subscription Activated
        
        Your {plan_name} subscription has been activated!
        You now have access to all features of the {plan_name} plan.
        
        Thank you for choosing ClientHunt!
        """
        
        return EmailService._send_email(user.email, subject, html_body, text_body)
    
    @staticmethod
    async def send_usage_warning_email(
        user_id: str,
        metric_type: str,
        current_usage: int,
        limit: int,
        db: Session
    ) -> bool:
        """
        Send usage warning email when approaching limits.
        
        Args:
            user_id: User UUID
            metric_type: Type of metric (e.g., "opportunities_per_month")
            current_usage: Current usage count
            limit: Usage limit
            db: Database session
            
        Returns:
            bool: True if sent successfully
        """
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return False
        
        percentage = int((current_usage / limit) * 100) if limit > 0 else 0
        
        subject = f"ClientHunt Usage Warning - {percentage}% Used"
        html_body = f"""
        <html>
          <body>
            <h2>Usage Warning</h2>
            <p>You've used {current_usage} of {limit} {metric_type.replace('_', ' ')}.</p>
            <p>You're at {percentage}% of your monthly limit.</p>
            <p>Consider upgrading your plan if you need more capacity.</p>
          </body>
        </html>
        """
        text_body = f"""
        Usage Warning
        
        You've used {current_usage} of {limit} {metric_type.replace('_', ' ')}.
        You're at {percentage}% of your monthly limit.
        
        Consider upgrading your plan if you need more capacity.
        """
        
        return EmailService._send_email(user.email, subject, html_body, text_body)
    
    @staticmethod
    async def send_leads_notification_email(
        user_email: str,
        user_name: str,
        keyword_search_name: str,
        leads_count: int,
        opportunities_url: str
    ) -> bool:
        """
        Send email notification when new leads are generated for scheduled keyword searches.
        
        Args:
            user_email: User's email address
            user_name: User's full name
            keyword_search_name: Name of the keyword search
            leads_count: Number of new leads/opportunities found
            opportunities_url: URL to view opportunities in the dashboard
            
        Returns:
            bool: True if sent successfully
        """
        subject = f"New Opportunities Found: {leads_count} leads from '{keyword_search_name}'"
        
        html_body = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
              <h2 style="color: #4F46E5;">New Opportunities Found!</h2>
              <p>Hi {user_name},</p>
              <p>Your scheduled keyword search <strong>"{keyword_search_name}"</strong> has found <strong>{leads_count}</strong> new opportunity/opportunities!</p>
              <div style="background-color: #F3F4F6; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <p style="margin: 0;"><strong>Search:</strong> {keyword_search_name}</p>
                <p style="margin: 5px 0 0 0;"><strong>New Opportunities:</strong> {leads_count}</p>
              </div>
              <p>Check them out in your dashboard:</p>
              <a href="{opportunities_url}" style="display: inline-block; background-color: #4F46E5; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 20px 0;">View Opportunities</a>
              <p style="margin-top: 30px; color: #6B7280; font-size: 14px;">
                This is an automated notification from your scheduled keyword search. 
                You can manage your searches and notification preferences in your dashboard.
              </p>
              <p style="margin-top: 20px; color: #6B7280; font-size: 14px;">
                Best regards,<br>
                The ClientHunt Team
              </p>
            </div>
          </body>
        </html>
        """
        
        text_body = f"""
        New Opportunities Found!

        Hi {user_name},

        Your scheduled keyword search "{keyword_search_name}" has found {leads_count} new opportunity/opportunities!

        Search: {keyword_search_name}
        New Opportunities: {leads_count}

        View them in your dashboard: {opportunities_url}

        This is an automated notification from your scheduled keyword search. 
        You can manage your searches and notification preferences in your dashboard.

        Best regards,
        The ClientHunt Team
        """
        
        # Use leads@clienthunt.app as the sender email
        from_email = "leads@clienthunt.app"
        from_name = "ClientHunt Leads"
        
        return EmailService._send_email(
            user_email, 
            subject, 
            html_body, 
            text_body,
            from_email=from_email,
            from_name=from_name
        )
    
    @staticmethod
    def send_support_thread_created_email(
        email: str,
        full_name: str,
        subject: str,
        thread_id: str
    ) -> bool:
        """
        Send email notification when a support thread is created.
        
        Args:
            email: User's email address
            full_name: User's full name
            subject: Support thread subject
            thread_id: Support thread ID
            
        Returns:
            bool: True if sent successfully
        """
        support_url = f"{settings.FRONTEND_URL}/dashboard/support?thread={thread_id}"
        
        subject_line = f"Support Request Created: {subject}"
        html_body = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
              <h2 style="color: #2563eb;">Support Request Created</h2>
              <p>Hi {full_name},</p>
              <p>Thank you for contacting us! We've received your support request and our team will get back to you shortly.</p>
              
              <div style="background-color: #f3f4f6; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <p style="margin: 0;"><strong>Subject:</strong> {subject}</p>
              </div>
              
              <p>You can view and respond to your support request by clicking the link below:</p>
              <p style="margin: 20px 0;">
                <a href="{support_url}" style="background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">View Support Request</a>
              </p>
              
              <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">
                Help will arrive shortly. Our support team typically responds within 24 hours.
              </p>
              
              <p style="color: #6b7280; font-size: 14px; margin-top: 20px;">
                If you have any urgent questions, please don't hesitate to reach out.
              </p>
              
              <p style="margin-top: 30px;">
                Best regards,<br>
                The ClientHunt Team
              </p>
            </div>
          </body>
        </html>
        """
        text_body = f"""
        Support Request Created
        
        Hi {full_name},
        
        Thank you for contacting us! We've received your support request and our team will get back to you shortly.
        
        Subject: {subject}
        
        You can view and respond to your support request by visiting:
        {support_url}
        
        Help will arrive shortly. Our support team typically responds within 24 hours.
        
        If you have any urgent questions, please don't hesitate to reach out.
        
        Best regards,
        The ClientHunt Team
        """
        
        return EmailService._send_email(email, subject_line, html_body, text_body)
    
    @staticmethod
    async def send_welcome_email(
        email: str,
        full_name: str,
        plan_name: str = "Free"
    ) -> bool:
        """
        Send welcome email after email verification.
        
        Args:
            email: User's email address
            full_name: User's full name
            plan_name: Subscription plan name (e.g., "Free", "Starter", "Professional", "Power")
            
        Returns:
            bool: True if sent successfully
        """
        logger.info(f"Sending welcome email to {email} for plan {plan_name}")
        dashboard_url = f"{settings.FRONTEND_URL}/dashboard"
        
        subject = f"Welcome to ClientHunt, {full_name}! 🎉"
        html_body = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
              <h2 style="color: #2563eb;">Welcome to ClientHunt, {full_name}! 🎉</h2>
              <p>Your email has been verified and your account is now active. You're all set to start finding amazing freelance opportunities!</p>
              
              <div style="background-color: #f0f9ff; border-left: 4px solid #2563eb; padding: 20px; margin: 30px 0; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #1e40af;">Your {plan_name} Plan Includes:</h3>
                <ul style="margin: 10px 0; padding-left: 20px;">
                  <li>AI-powered lead generation from Reddit</li>
                  <li>Real-time opportunity notifications</li>
                  <li>Advanced filtering and search</li>
                  <li>Analytics dashboard</li>
                  <li>CSV/PDF export</li>
                  <li>Mobile-responsive dashboard</li>
                </ul>
              </div>
              
              <h3 style="color: #2563eb; margin-top: 30px;">Get Started:</h3>
              <ol style="line-height: 2;">
                <li><strong>Create your first keyword search</strong> - Monitor Reddit for opportunities matching your skills</li>
                <li><strong>Set up notifications</strong> - Get notified instantly when new opportunities are found</li>
                <li><strong>Explore the dashboard</strong> - View analytics, manage opportunities, and track your usage</li>
              </ol>
              
              <p style="margin: 30px 0;">
                <a href="{dashboard_url}" style="background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">Go to Dashboard</a>
              </p>
              
              <div style="background-color: #f9fafb; padding: 20px; border-radius: 8px; margin: 30px 0;">
                <h4 style="margin-top: 0; color: #1e40af;">💡 Pro Tips:</h4>
                <ul style="margin: 10px 0; padding-left: 20px;">
                  <li>Use specific keywords related to your skills (e.g., "React developer", "Python freelancer")</li>
                  <li>Monitor multiple subreddits to maximize opportunities</li>
                  <li>Set up filters for budget, location, and urgency to find the best matches</li>
                  <li>Check your dashboard regularly for new opportunities</li>
                </ul>
              </div>
              
              <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">
                Need help? Check out our <a href="{settings.FRONTEND_URL}/dashboard/support" style="color: #2563eb;">support center</a> or reply to this email.
              </p>
              
              <p style="margin-top: 30px;">
                Happy hunting!<br>
                <strong>The ClientHunt Team</strong>
              </p>
            </div>
          </body>
        </html>
        """
        text_body = f"""
        Welcome to ClientHunt, {full_name}! 🎉
        
        Your email has been verified and your account is now active. You're all set to start finding amazing freelance opportunities!
        
        Your {plan_name} Plan Includes:
        - AI-powered lead generation from Reddit
        - Real-time opportunity notifications
        - Advanced filtering and search
        - Analytics dashboard
        - CSV/PDF export
        - Mobile-responsive dashboard
        
        Get Started:
        1. Create your first keyword search - Monitor Reddit for opportunities matching your skills
        2. Set up notifications - Get notified instantly when new opportunities are found
        3. Explore the dashboard - View analytics, manage opportunities, and track your usage
        
        Go to Dashboard: {dashboard_url}
        
        💡 Pro Tips:
        - Use specific keywords related to your skills (e.g., "React developer", "Python freelancer")
        - Monitor multiple subreddits to maximize opportunities
        - Set up filters for budget, location, and urgency to find the best matches
        - Check your dashboard regularly for new opportunities
        
        Need help? Check out our support center: {settings.FRONTEND_URL}/dashboard/support
        
        Happy hunting!
        The ClientHunt Team
        """
        
        # Send from welcome@
        try:
            result = EmailService._send_email(
                email,
                subject,
                html_body,
                text_body,
                from_email=settings.SMTP_WELCOME_EMAIL,
                from_name=settings.SMTP_FROM_NAME
            )
            if result:
                logger.info(f"Welcome email sent successfully to {email}")
            else:
                logger.warning(f"Failed to send welcome email to {email}")
            return result
        except Exception as e:
            logger.error(f"Error sending welcome email to {email}: {str(e)}")
            return False
    
    @staticmethod
    async def send_payment_receipt_email(
        payment_id: str,
        user_email: str,
        user_name: str,
        amount_cents: int,
        currency: str,
        plan: str,
        billing_period: str,
        transaction_id: Optional[str] = None,
        db: Optional[Session] = None
    ) -> bool:
        """
        Send payment receipt email with PDF invoice attachment.
        
        Args:
            payment_id: Payment UUID
            user_email: User's email address
            user_name: User's full name
            amount_cents: Payment amount in cents
            currency: Currency code (e.g., USD)
            plan: Subscription plan name
            billing_period: Billing period (monthly/yearly)
            transaction_id: Paddle transaction ID (optional)
            db: Database session (optional, for fetching additional details)
            
        Returns:
            bool: True if sent successfully
        """
        try:
            # Format amount
            amount_dollars = amount_cents / 100
            currency_symbol = "$" if currency == "USD" else currency
            formatted_amount = f"{currency_symbol}{amount_dollars:.2f}"
            
            # Format billing period
            billing_period_display = billing_period.capitalize()
            if billing_period == "monthly":
                billing_period_display = "Monthly"
            elif billing_period == "yearly":
                billing_period_display = "Annual"
            
            # Generate PDF invoice
            pdf_content = EmailService._generate_payment_receipt_pdf(
                payment_id=payment_id,
                user_name=user_name,
                user_email=user_email,
                amount_cents=amount_cents,
                currency=currency,
                plan=plan,
                billing_period=billing_period,
                transaction_id=transaction_id
            )
            
            subject = f"Payment Receipt - {formatted_amount} - ClientHunt"
            
            html_body = f"""
            <html>
              <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                  <h2 style="color: #2563eb;">Payment Receipt</h2>
                  <p>Hi {user_name},</p>
                  <p>Thank you for your payment! Your subscription has been successfully processed.</p>
                  
                  <div style="background-color: #f3f4f6; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3 style="margin-top: 0; color: #1e40af;">Payment Details</h3>
                    <table style="width: 100%; border-collapse: collapse;">
                      <tr>
                        <td style="padding: 8px 0; font-weight: bold;">Amount:</td>
                        <td style="padding: 8px 0; text-align: right;">{formatted_amount}</td>
                      </tr>
                      <tr>
                        <td style="padding: 8px 0; font-weight: bold;">Plan:</td>
                        <td style="padding: 8px 0; text-align: right;">{plan.capitalize()} ({billing_period_display})</td>
                      </tr>
                      <tr>
                        <td style="padding: 8px 0; font-weight: bold;">Payment ID:</td>
                        <td style="padding: 8px 0; text-align: right; font-family: monospace; font-size: 12px;">{payment_id}</td>
                      </tr>
                      {f'<tr><td style="padding: 8px 0; font-weight: bold;">Transaction ID:</td><td style="padding: 8px 0; text-align: right; font-family: monospace; font-size: 12px;">{transaction_id}</td></tr>' if transaction_id else ''}
                      <tr>
                        <td style="padding: 8px 0; font-weight: bold;">Date:</td>
                        <td style="padding: 8px 0; text-align: right;">{datetime.utcnow().strftime('%B %d, %Y')}</td>
                      </tr>
                    </table>
                  </div>
                  
                  <p>A detailed receipt has been attached to this email as a PDF.</p>
                  
                  <p style="margin-top: 30px;">
                    <a href="{settings.FRONTEND_URL}/dashboard/subscription" style="background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">View Subscription</a>
                  </p>
                  
                  <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">
                    If you have any questions about this payment, please contact our support team.
                  </p>
                  
                  <p style="margin-top: 30px;">
                    Best regards,<br>
                    <strong>The ClientHunt Team</strong>
                  </p>
                </div>
              </body>
            </html>
            """
            
            text_body = f"""
            Payment Receipt
            
            Hi {user_name},
            
            Thank you for your payment! Your subscription has been successfully processed.
            
            Payment Details:
            - Amount: {formatted_amount}
            - Plan: {plan.capitalize()} ({billing_period_display})
            - Payment ID: {payment_id}
            {f'- Transaction ID: {transaction_id}' if transaction_id else ''}
            - Date: {datetime.utcnow().strftime('%B %d, %Y')}
            
            A detailed receipt has been attached to this email as a PDF.
            
            View your subscription: {settings.FRONTEND_URL}/dashboard/subscription
            
            If you have any questions about this payment, please contact our support team.
            
            Best regards,
            The ClientHunt Team
            """
            
            # Create message with PDF attachment
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
            msg['To'] = user_email
            
            # Add text and HTML parts
            text_part = MIMEText(text_body, 'plain')
            msg.attach(text_part)
            
            html_part = MIMEText(html_body, 'html')
            msg.attach(html_part)
            
            # Attach PDF
            pdf_attachment = MIMEBase('application', 'pdf')
            pdf_attachment.set_payload(pdf_content)
            encoders.encode_base64(pdf_attachment)
            pdf_attachment.add_header(
                'Content-Disposition',
                f'attachment; filename="receipt-{payment_id}.pdf"'
            )
            msg.attach(pdf_attachment)
            
            # Send email
            server = EmailService._create_smtp_connection()
            try:
                server.send_message(msg)
                logger.info(f"Payment receipt email sent successfully to {user_email} (payment_id: {payment_id})")
                return True
            finally:
                try:
                    server.quit()
                except Exception:
                    pass
            
        except Exception as e:
            logger.error(f"Failed to send payment receipt email to {user_email}: {str(e)}", exc_info=True)
            return False
    
    @staticmethod
    def _generate_payment_receipt_pdf(
        payment_id: str,
        user_name: str,
        user_email: str,
        amount_cents: int,
        currency: str,
        plan: str,
        billing_period: str,
        transaction_id: Optional[str] = None
    ) -> bytes:
        """
        Generate PDF receipt for payment.
        
        Uses reportlab if available, otherwise generates a simple text-based PDF.
        
        Args:
            payment_id: Payment UUID
            user_name: User's full name
            user_email: User's email address
            amount_cents: Payment amount in cents
            currency: Currency code
            plan: Subscription plan name
            billing_period: Billing period
            transaction_id: Paddle transaction ID (optional)
            
        Returns:
            bytes: PDF content
        """
        if REPORTLAB_AVAILABLE:
            try:
                # Use reportlab for better PDF generation
                buffer = io.BytesIO()
                doc = SimpleDocTemplate(buffer, pagesize=letter)
                story = []
                styles = getSampleStyleSheet()
                
                # Custom styles
                title_style = ParagraphStyle(
                    'CustomTitle',
                    parent=styles['Heading1'],
                    fontSize=24,
                    textColor=colors.HexColor('#2563eb'),
                    spaceAfter=30,
                    alignment=TA_CENTER
                )
                
                heading_style = ParagraphStyle(
                    'CustomHeading',
                    parent=styles['Heading2'],
                    fontSize=14,
                    textColor=colors.HexColor('#1e40af'),
                    spaceAfter=12
                )
                
                # Title
                story.append(Paragraph("Payment Receipt", title_style))
                story.append(Spacer(1, 0.3*inch))
                
                # Company info
                story.append(Paragraph("<b>ClientHunt</b>", styles['Normal']))
                story.append(Paragraph("Invoice & Receipt", styles['Normal']))
                story.append(Spacer(1, 0.2*inch))
                
                # Payment details
                amount_dollars = amount_cents / 100
                currency_symbol = "$" if currency == "USD" else currency
                formatted_amount = f"{currency_symbol}{amount_dollars:.2f}"
                
                data = [
                    ['Payment ID:', payment_id],
                    ['Date:', datetime.utcnow().strftime('%B %d, %Y')],
                    ['Amount:', formatted_amount],
                    ['Plan:', f"{plan.capitalize()} ({billing_period.capitalize()})"],
                ]
                
                if transaction_id:
                    data.append(['Transaction ID:', transaction_id])
                
                data.append(['Customer:', user_name])
                data.append(['Email:', user_email])
                
                table = Table(data, colWidths=[2*inch, 4*inch])
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f3f4f6')),
                    ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
                    ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                    ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                    ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                    ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
                    ('FONTSIZE', (0, 0), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
                    ('TOPPADDING', (0, 0), (-1, -1), 12),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ]))
                
                story.append(table)
                story.append(Spacer(1, 0.3*inch))
                
                # Footer
                story.append(Spacer(1, 0.5*inch))
                story.append(Paragraph("Thank you for your business!", styles['Normal']))
                story.append(Paragraph("If you have any questions, please contact support.", styles['Normal']))
                
                # Build PDF
                doc.build(story)
                pdf_content = buffer.getvalue()
                buffer.close()
                
                return pdf_content
            except Exception as e:
                logger.error(f"Error generating PDF receipt with reportlab: {str(e)}", exc_info=True)
                # Fallback to simple PDF
                return EmailService._generate_simple_pdf_receipt(
                    payment_id, user_name, user_email, amount_cents, currency, plan, billing_period, transaction_id
                )
        else:
            # Fallback: Generate simple text-based PDF if reportlab not available
            logger.warning("reportlab not available, generating simple PDF")
            return EmailService._generate_simple_pdf_receipt(
                payment_id, user_name, user_email, amount_cents, currency, plan, billing_period, transaction_id
            )
    
    @staticmethod
    def _generate_simple_pdf_receipt(
        payment_id: str,
        user_name: str,
        user_email: str,
        amount_cents: int,
        currency: str,
        plan: str,
        billing_period: str,
        transaction_id: Optional[str] = None
    ) -> bytes:
        """
        Generate a simple text-based PDF receipt (fallback).
        
        Args:
            Same as _generate_payment_receipt_pdf
            
        Returns:
            bytes: Simple PDF content
        """
        amount_dollars = amount_cents / 100
        currency_symbol = "$" if currency == "USD" else currency
        formatted_amount = f"{currency_symbol}{amount_dollars:.2f}"
        
        # Simple PDF structure (minimal PDF format)
        receipt_text = f"""
PAYMENT RECEIPT
ClientHunt

Payment ID: {payment_id}
Date: {datetime.utcnow().strftime('%B %d, %Y')}
Amount: {formatted_amount}
Plan: {plan.capitalize()} ({billing_period.capitalize()})
{f'Transaction ID: {transaction_id}' if transaction_id else ''}

Customer: {user_name}
Email: {user_email}

Thank you for your business!
"""
        
        # Convert to simple PDF format (minimal PDF structure)
        # This is a very basic PDF - for production, use reportlab
        # Fix f-string: extract backslash operations outside f-string
        escaped_receipt_text = receipt_text.replace('(', '\\(').replace(')', '\\)')
        receipt_length = len(receipt_text)
        startxref_value = 400 + receipt_length
        
        pdf_content = f"""%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj
2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj
3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [0 0 612 792]
/Contents 4 0 R
/Resources <<
/Font <<
/F1 <<
/Type /Font
/Subtype /Type1
/BaseFont /Helvetica
>>
>>
>>
>>
endobj
4 0 obj
<<
/Length {receipt_length}
>>
stream
BT
/F1 12 Tf
100 700 Td
({escaped_receipt_text}) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000300 00000 n
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
{startxref_value}
%%EOF
""".encode('utf-8')
        
        return pdf_content

