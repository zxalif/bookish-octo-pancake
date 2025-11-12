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
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import secrets
import hashlib

from core.config import get_settings
from core.logger import get_logger
from sqlalchemy.orm import Session

settings = get_settings()
logger = get_logger(__name__)


class EmailService:
    """Service for sending emails."""
    
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
        Send email via SMTP.
        
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
        try:
            # Use provided from_email/from_name or fall back to defaults
            sender_email = from_email or settings.SMTP_FROM_EMAIL
            sender_name = from_name or settings.SMTP_FROM_NAME
            
            # Create message
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
            
            # Send email
            server = EmailService._create_smtp_connection()
            try:
                server.send_message(msg)
                logger.info(f"Email sent successfully to {to_email} (subject: {subject})")
                return True
            finally:
                try:
                    server.quit()
                except Exception:
                    pass  # Ignore errors when closing connection
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(
                f"SMTP authentication failed when sending to {to_email}. "
                f"This might be due to: 1) IP restrictions on email provider (PrivateEmail may block VPS IPs), "
                f"2) Different network in production vs localhost, 3) Account security settings, "
                f"4) Need to whitelist VPS IP in email provider settings. "
                f"Error code: {e.smtp_code}, Error: {e.smtp_error}"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}", exc_info=True)
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
        html_body = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
              <h2 style="color: #2563eb;">Welcome to ClientHunt!</h2>
              <p>Thank you for signing up! Please verify your email address to complete your registration.</p>
              <p style="margin: 30px 0;">
                <a href="{verification_url}" style="background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">Verify Email Address</a>
              </p>
              <p>Or copy and paste this URL into your browser:</p>
              <p style="background-color: #f3f4f6; padding: 10px; border-radius: 4px; word-break: break-all; font-size: 12px; color: #6b7280;">{verification_url}</p>
              <p style="color: #6b7280; font-size: 14px;">This link will expire in 24 hours.</p>
              <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">If you didn't create an account, please ignore this email.</p>
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
        from models.user import User
        from services.auth_service import AuthService
        
        user = db.query(User).filter(User.email == email).first()
        if not user:
            # Don't reveal if email exists (security best practice)
            return True
        
        # Generate reset token if not provided
        if not token:
            token = AuthService.generate_password_reset_token(user.id)
        
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
        
        subject = "Reset Your ClientHunt Password"
        html_body = f"""
        <html>
          <body>
            <h2>Password Reset Request</h2>
            <p>You requested to reset your password. Click the link below to reset it:</p>
            <p><a href="{reset_url}">Reset Password</a></p>
            <p>Or copy and paste this URL into your browser:</p>
            <p>{reset_url}</p>
            <p>This link will expire in 1 hour.</p>
            <p>If you didn't request a password reset, please ignore this email.</p>
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
        from models.user import User
        
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
        from models.user import User
        
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

