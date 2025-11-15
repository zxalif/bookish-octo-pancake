#!/usr/bin/env python3
"""
Manual Email Verification Script

Manually verifies a user's email address by email.
This follows the same flow as normal email verification:
- Marks email as verified
- Creates audit log entry
- Ensures user has a free subscription (30-day trial) if they don't have one
- Sends welcome email

Usage: python scripts/verify_email_manual.py <email>
"""

import sys
import os
import asyncio

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from core.database import SessionLocal
from services.auth_service import AuthService
from services.email_service import EmailService
from services.subscription_service import SubscriptionService
from models.user_audit_log import UserAuditLog
from core.logger import get_logger

logger = get_logger(__name__)


async def verify_email_manual(email: str):
    """
    Manually verify a user's email address.
    
    This follows the same flow as normal email verification:
    1. Marks email as verified
    2. Creates audit log entry
    3. Sends welcome email
    
    Args:
        email: User email address
        
    Returns:
        bool: True if verification successful, False otherwise
    """
    db: Session = SessionLocal()
    
    try:
        # Get user by email
        user = AuthService.get_user_by_email(email, db)
        
        if not user:
            print(f"❌ User with email '{email}' not found")
            return False
        
        # Check if already verified
        if user.is_verified:
            print(f"ℹ️  User '{email}' is already verified")
            print(f"   User ID: {user.id}")
            print(f"   Full Name: {user.full_name}")
            response = input("Do you want to resend the welcome email? (y/n): ")
            if response.lower() != 'y':
                print("No action taken.")
                return True  # Already verified, so this is success
            
            # User wants to resend welcome email only
            print("Resending welcome email...")
            # Ensure user has a subscription
            active_subscription = SubscriptionService.get_active_subscription(user.id, db)
            if not active_subscription:
                print(f"📦 Creating free subscription (30-day trial)...")
                active_subscription = SubscriptionService.create_free_subscription(user.id, db)
                print(f"✅ Free subscription created (expires in 30 days)")
            plan_name = "Free"
            if active_subscription:
                plan_name = active_subscription.plan.value.replace("_", " ").title()
            
            try:
                email_sent = await EmailService.send_welcome_email(
                    email=user.email,
                    full_name=user.full_name,
                    plan_name=plan_name
                )
                if email_sent:
                    print(f"✅ Welcome email sent successfully")
                else:
                    print(f"⚠️  Welcome email failed to send")
            except Exception as e:
                print(f"⚠️  Error sending welcome email: {str(e)}")
                logger.error(f"Error sending welcome email to {user.email}: {str(e)}", exc_info=True)
            
            return True
        
        # User is not verified - proceed with verification flow
        print(f"📧 Verifying email for user '{email}'...")
        print(f"   User ID: {user.id}")
        print(f"   Full Name: {user.full_name}")
        
        # Mark email as verified
        user.is_verified = True
        
        # Create audit log entry for email verification
        # Use "verify_email" action to match normal verification flow
        audit_log = UserAuditLog(
            user_id=user.id,
            action="verify_email",
            ip_address=None,  # Script execution, no IP
            user_agent="manual-verification-script",
            details=f"Email verified manually via script: {email}"
        )
        db.add(audit_log)
        
        # Commit changes
        db.commit()
        db.refresh(user)
        
        print(f"✅ Email marked as verified")
        
        # Ensure user has a subscription (free trial if they don't have one)
        # This matches the normal flow where subscriptions are auto-created
        active_subscription = SubscriptionService.get_active_subscription(user.id, db)
        if not active_subscription:
            print(f"📦 Creating free subscription (30-day trial)...")
            active_subscription = SubscriptionService.create_free_subscription(user.id, db)
            print(f"✅ Free subscription created (expires in 30 days)")
        else:
            print(f"📦 User already has an active subscription")
        
        # Get user's subscription plan name for welcome email
        plan_name = "Free"
        if active_subscription:
            # plan is an enum, use .value to get the string value
            plan_name = active_subscription.plan.value.replace("_", " ").title()
        
        print(f"   Subscription Plan: {plan_name}")
        if active_subscription and active_subscription.current_period_end:
            from datetime import datetime
            days_remaining = (active_subscription.current_period_end - datetime.utcnow()).days
            print(f"   Days Remaining: {days_remaining}")
        
        # Send welcome email (non-blocking, don't fail verification if email fails)
        print(f"📧 Sending welcome email...")
        try:
            email_sent = await EmailService.send_welcome_email(
                email=user.email,
                full_name=user.full_name,
                plan_name=plan_name
            )
            if email_sent:
                print(f"✅ Welcome email sent successfully")
            else:
                print(f"⚠️  Welcome email failed to send, but verification succeeded")
                logger.warning(f"Welcome email failed to send to {user.email}, but verification succeeded")
        except Exception as e:
            print(f"⚠️  Error sending welcome email: {str(e)}")
            print(f"   Verification succeeded, but welcome email was not sent")
            logger.error(f"Error sending welcome email to {user.email}: {str(e)}", exc_info=True)
            # Don't fail verification if email sending fails (same as normal flow)
        
        print(f"\n✅ Email verification completed successfully!")
        print(f"   User: {user.full_name} ({user.email})")
        print(f"   User ID: {user.id}")
        print(f"   Verified: {user.is_verified}")
        print(f"   Plan: {plan_name}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error verifying email: {str(e)}", exc_info=True)
        print(f"❌ Error: {str(e)}")
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_email_manual.py <email>")
        print("Example: python scripts/verify_email_manual.py user@example.com")
        print("\nThis script will:")
        print("  1. Mark the user's email as verified")
        print("  2. Create an audit log entry")
        print("  3. Create a free subscription (30-day trial) if user doesn't have one")
        print("  4. Send a welcome email")
        sys.exit(1)
    
    email = sys.argv[1]
    
    # Validate email format (basic check)
    if "@" not in email or "." not in email.split("@")[1]:
        print(f"❌ Invalid email format: '{email}'")
        sys.exit(1)
    
    # Run async function
    success = asyncio.run(verify_email_manual(email))
    sys.exit(0 if success else 1)

