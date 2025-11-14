#!/usr/bin/env python3
"""
Set Admin User Script

Sets a user as admin by email address.
Usage: python scripts/set_admin_user.py <email>
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from core.database import SessionLocal
from models.user import User
from core.logger import get_logger

logger = get_logger(__name__)


def set_admin_user(email: str, is_admin: bool = True):
    """
    Set a user as admin.
    
    Args:
        email: User email address
        is_admin: Whether to set as admin (default: True)
    """
    db: Session = SessionLocal()
    
    try:
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            print(f"❌ User with email '{email}' not found")
            return False
        
        user.is_admin = is_admin
        db.commit()
        
        status = "admin" if is_admin else "regular user"
        print(f"✅ User '{email}' is now a {status}")
        return True
        
    except Exception as e:
        logger.error(f"Error setting admin user: {str(e)}")
        db.rollback()
        print(f"❌ Error: {str(e)}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/set_admin_user.py <email> [true|false]")
        print("Example: python scripts/set_admin_user.py admin@clienthunt.app")
        print("Example: python scripts/set_admin_user.py admin@clienthunt.app false  # Remove admin")
        sys.exit(1)
    
    email = sys.argv[1]
    is_admin = True
    
    if len(sys.argv) > 2:
        is_admin = sys.argv[2].lower() in ['true', '1', 'yes']
    
    set_admin_user(email, is_admin)

