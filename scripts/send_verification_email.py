#!/usr/bin/env python3
"""
Send Verification Email Script

Sends a verification email to a user by email address.
Usage: python scripts/send_verification_email.py <email>
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
from core.logger import get_logger

logger = get_logger(__name__)


async def send_verification_email(email: str):
    """
    Send verification email to a user.
    
    Args:
        email: User email address
        
    Returns:
        bool: True if email sent successfully, False otherwise
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
            print(f"⚠️  User '{email}' is already verified")
            response = input("Do you want to send a verification email anyway? (y/n): ")
            if response.lower() != 'y':
                print("Cancelled.")
                return False
        
        # Generate verification token
        verification_token = AuthService.generate_email_verification_token(user.id)
        
        # Send verification email
        print(f"📧 Sending verification email to '{email}'...")
        email_sent = await EmailService.send_verification_email(
            email=user.email,
            user_id=user.id,
            token=verification_token
        )
        
        if email_sent:
            print(f"✅ Verification email sent successfully to '{email}'")
            print(f"   User ID: {user.id}")
            print(f"   Token expires in 24 hours")
            return True
        else:
            print(f"❌ Failed to send verification email to '{email}'")
            logger.error(f"Failed to send verification email to {email}")
            return False
        
    except Exception as e:
        logger.error(f"Error sending verification email: {str(e)}", exc_info=True)
        print(f"❌ Error: {str(e)}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/send_verification_email.py <email>")
        print("Example: python scripts/send_verification_email.py user@example.com")
        sys.exit(1)
    
    email = sys.argv[1]
    
    # Validate email format (basic check)
    if "@" not in email or "." not in email.split("@")[1]:
        print(f"❌ Invalid email format: '{email}'")
        sys.exit(1)
    
    # Run async function
    success = asyncio.run(send_verification_email(email))
    sys.exit(0 if success else 1)

