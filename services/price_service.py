"""
Price Service

Handles price management and retrieval from database.
"""

from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from models.price import Price, BillingPeriod
from models.subscription import SubscriptionPlan


class PriceService:
    """Service for managing prices in the database."""
    
    @staticmethod
    def get_price_by_plan_and_period(
        plan: str,
        billing_period: str,
        db: Session
    ) -> Optional[Price]:
        """
        Get active price for a plan and billing period.
        
        Args:
            plan: Subscription plan (starter, professional, power)
            billing_period: Billing period (monthly, yearly)
            db: Database session
            
        Returns:
            Price object or None if not found
        """
        try:
            billing_period_enum = BillingPeriod(billing_period.lower())
        except ValueError:
            return None
        
        price = db.query(Price).filter(
            Price.plan == plan,
            Price.billing_period == billing_period_enum,
            Price.is_active == True
        ).first()
        
        return price
    
    @staticmethod
    def get_price_by_paddle_id(
        paddle_price_id: str,
        db: Session
    ) -> Optional[Price]:
        """
        Get price by Paddle price ID.
        
        Args:
            paddle_price_id: Paddle price ID
            db: Database session
            
        Returns:
            Price object or None if not found
        """
        return db.query(Price).filter(
            Price.paddle_price_id == paddle_price_id,
            Price.is_active == True
        ).first()
    
    @staticmethod
    def get_all_active_prices(db: Session) -> list[Price]:
        """
        Get all active prices.
        
        Args:
            db: Database session
            
        Returns:
            List of active Price objects
        """
        return db.query(Price).filter(Price.is_active == True).all()
    
    @staticmethod
    def get_prices_by_plan(plan: str, db: Session) -> list[Price]:
        """
        Get all active prices for a plan (monthly and yearly).
        
        Args:
            plan: Subscription plan
            db: Database session
            
        Returns:
            List of Price objects
        """
        return db.query(Price).filter(
            Price.plan == plan,
            Price.is_active == True
        ).all()
    
    @staticmethod
    def create_or_update_price(
        plan: str,
        billing_period: str,
        paddle_price_id: str,
        amount: int,
        currency: str = "USD",
        paddle_product_id: Optional[str] = None,
        db: Session = None
    ) -> Price:
        """
        Create or update a price in the database.
        
        Args:
            plan: Subscription plan
            billing_period: Billing period (monthly, yearly)
            paddle_price_id: Paddle price ID
            amount: Amount in cents
            currency: Currency code
            paddle_product_id: Paddle product ID (optional)
            db: Database session
            
        Returns:
            Created or updated Price object
        """
        try:
            billing_period_enum = BillingPeriod(billing_period.lower())
        except ValueError:
            raise ValueError(f"Invalid billing period: {billing_period}")
        
        # Check if price already exists
        existing = db.query(Price).filter(
            Price.paddle_price_id == paddle_price_id
        ).first()
        
        if existing:
            # Update existing price
            existing.plan = plan
            existing.billing_period = billing_period_enum
            existing.amount = amount
            existing.currency = currency
            existing.paddle_product_id = paddle_product_id
            existing.is_active = True
            db.commit()
            db.refresh(existing)
            return existing
        
        # Ensure only one active price per plan/billing_period combination
        # Deactivate any existing active prices for this plan/billing_period
        db.query(Price).filter(
            Price.plan == plan,
            Price.billing_period == billing_period_enum,
            Price.is_active == True
        ).update({"is_active": False})
        db.commit()
        
        # Create new price
        price = Price(
            plan=plan,
            billing_period=billing_period_enum,
            paddle_price_id=paddle_price_id,
            paddle_product_id=paddle_product_id,
            amount=amount,
            currency=currency,
            is_active=True
        )
        
        db.add(price)
        db.commit()
        db.refresh(price)
        
        return price

