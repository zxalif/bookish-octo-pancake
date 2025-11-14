#!/usr/bin/env python3
"""
Paddle Products Setup Script

This script creates products and prices in Paddle using the official Paddle Python SDK.
It creates:
- 3 Products: Starter, Professional, Power
- 2 Prices per product: Monthly and Yearly (with 2 months free for annual)

Usage:
    python scripts/setup_paddle_products.py

Environment Variables Required:
    - PADDLE_API_KEY: Your Paddle API key
    - PADDLE_ENVIRONMENT: 'sandbox' or 'live' (default: 'sandbox')
"""

import os
import sys
import asyncio
from pathlib import Path
from typing import Dict, Optional

# Add parent directory to path to import core modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from paddle_billing import Client, Environment, Options
from paddle_billing.Entities.Shared.TaxCategory import TaxCategory
from paddle_billing.Entities.Shared.TimePeriod import TimePeriod
from paddle_billing.Resources.Products.Operations import CreateProduct
from paddle_billing.Resources.Prices.Operations import CreatePrice
from paddle_billing.Exceptions.ApiError import ApiError

from core.config import get_settings
from core.database import SessionLocal
from services.price_service import PriceService

settings = get_settings()


class PaddleSetup:
    """Helper class for setting up Paddle products and prices using the official SDK."""
    
    def __init__(self):
        self.api_key = settings.PADDLE_API_KEY
        self.environment = settings.PADDLE_ENVIRONMENT or "sandbox"
        
        if not self.api_key:
            raise ValueError("PADDLE_API_KEY environment variable is required")
        
        # Initialize Paddle SDK client with explicit environment
        # Use sandbox in development or when explicitly set
        paddle_env = self.environment.lower()
        if paddle_env == "sandbox" or settings.ENVIRONMENT == "development":
            # Use sandbox environment
            options = Options(environment=Environment.SANDBOX)
            self.paddle = Client(self.api_key, options=options)
            print(f"✅ Paddle client initialized with SANDBOX environment")
        else:
            # Use production environment
            options = Options(environment=Environment.PRODUCTION)
            self.paddle = Client(self.api_key, options=options)
            print(f"✅ Paddle client initialized with PRODUCTION environment")
    
    def create_product(
        self,
        name: str,
        description: str
    ) -> Dict:
        """
        Create a product in Paddle using the SDK.
        
        Args:
            name: Product name
            description: Product description
            
        Returns:
            dict: Created product data
        """
        try:
            created_product = self.paddle.products.create(CreateProduct(
                name=name,
                description=description,
                tax_category=TaxCategory.Standard
            ))
            
            print(f"✅ Created product: {name} (ID: {created_product.id})")
            return {
                "id": created_product.id,
                "name": created_product.name,
                "description": created_product.description
            }
        except ApiError as e:
            # Check if product already exists
            if e.error_code == "conflict" or "already exists" in str(e).lower():
                print(f"⚠️  Product '{name}' might already exist. Checking existing products...")
                # Try to find existing product
                existing = self.find_product_by_name(name)
                if existing:
                    print(f"✅ Found existing product: {name} (ID: {existing['id']})")
                    return existing
            raise Exception(f"Failed to create product '{name}': {e.error_code} - {e}")
    
    def find_product_by_name(self, name: str) -> Optional[Dict]:
        """Find a product by name using the SDK."""
        try:
            products = self.paddle.products.list()
            for product in products:
                if product.name == name:
                    return {
                        "id": product.id,
                        "name": product.name,
                        "description": product.description
                    }
            return None
        except Exception as e:
            print(f"⚠️  Error finding product: {e}")
            return None
    
    def create_price(
        self,
        product_id: str,
        description: str,
        amount: int,  # Amount in cents
        currency: str = "USD",
        billing_cycle: str = "month",  # "month" or "year"
        interval: int = 1
    ) -> Dict:
        """
        Create a price for a product using the SDK.
        
        Args:
            product_id: Product ID
            description: Price description
            amount: Amount in cents (e.g., 1900 for $19.00)
            currency: Currency code (default: "USD")
            billing_cycle: Billing cycle - "month" or "year"
            interval: Billing interval (default: 1)
            
        Returns:
            dict: Created price data
        """
        try:
            # Map billing cycle to Paddle's TimePeriod enum
            if billing_cycle == "year":
                billing_period = TimePeriod.Year
            else:
                billing_period = TimePeriod.Month
            
            # Create price using SDK
            # Set quantity limits to lock quantity to 1 for subscriptions
            # This prevents customers from changing quantity in checkout
            # Reference: https://developer.paddle.com/build/checkout/pass-update-checkout-items
            #
            # IMPORTANT: VAT/TAX HANDLING
            # - Paddle acts as Merchant of Record and handles ALL tax/VAT calculations
            # - The 'amount' parameter should be the BASE PRICE (excluding VAT)
            # - Paddle automatically adds VAT/tax based on customer's location
            # - VAT is NOT deducted from your billing amount - it's added to customer's total
            # - You receive the full base price amount, Paddle handles tax collection/remittance
            # Reference: https://www.paddle.com/help/sell/tax/how-paddle-handles-vat-on-your-behalf
            created_price = self.paddle.prices.create(CreatePrice(
                product_id=product_id,
                description=description,
                unit_price={
                    "amount": str(amount),  # Base price excluding VAT (e.g., $19.00 = 1900 cents)
                    "currency_code": currency
                },
                billing_cycle={
                    "interval": billing_period,
                    "frequency": interval
                },
                quantity={
                    "minimum": 1,
                    "maximum": 1
                }
                # Note: tax_mode is not needed - Paddle handles tax automatically as Merchant of Record
            ))
            
            print(f"   ✅ Created price: {description} (ID: {created_price.id}) - ${amount/100:.2f}/{billing_cycle}")
            return {
                "id": created_price.id,
                "product_id": created_price.product_id,
                "description": created_price.description,
                "unit_price": {
                    "amount": created_price.unit_price.amount,
                    "currency_code": created_price.unit_price.currency_code
                }
            }
        except ApiError as e:
            raise Exception(f"Failed to create price '{description}': {e.error_code} - {e}")
    
    def setup_all_products(self) -> Dict[str, Dict[str, str]]:
        """
        Create all products and prices for ClientHunt using the SDK.
        
        Returns:
            dict: Dictionary mapping plan names to price IDs (monthly and yearly)
        """
        print(f"\n🚀 Setting up Paddle products in {self.environment.upper()} environment...\n")
        
        # Pricing configuration
        plans = {
            "starter": {
                "name": "Starter",
                "description": "Perfect for freelancers just getting started",
                "monthly_price": 1900,  # $19.00 in cents
                "yearly_price": 19000,   # $190.00 in cents (2 months free)
            },
            "professional": {
                "name": "Professional",
                "description": "For serious freelancers growing their business",
                "monthly_price": 3900,  # $39.00 in cents
                "yearly_price": 39000,  # $390.00 in cents (2 months free)
            },
            "power": {
                "name": "Power",
                "description": "For power users and agencies",
                "monthly_price": 7900,  # $79.00 in cents
                "yearly_price": 79000,  # $790.00 in cents (2 months free)
            }
        }
        
        price_ids = {}
        
        # Create products and prices
        for plan_key, plan_data in plans.items():
            print(f"\n📦 Creating {plan_data['name']} plan...")
            
            # Create product
            product = self.create_product(
                name=f"ClientHunt {plan_data['name']}",
                description=plan_data['description']
            )
            product_id = product.get("id")
            
            if not product_id:
                print(f"❌ Failed to get product ID for {plan_data['name']}")
                continue
            
            # Create monthly price
            monthly_price = self.create_price(
                product_id=product_id,
                description=f"{plan_data['name']} - Monthly",
                amount=plan_data['monthly_price'],
                billing_cycle="month",
                interval=1
            )
            
            # Create yearly price
            yearly_price = self.create_price(
                product_id=product_id,
                description=f"{plan_data['name']} - Yearly",
                amount=plan_data['yearly_price'],
                billing_cycle="year",
                interval=1
            )
            
            price_ids[plan_key] = {
                "product_id": product_id,
                "monthly_price_id": monthly_price.get("id"),
                "yearly_price_id": yearly_price.get("id")
            }
        
        return price_ids
    
    def get_plan_amount(self, plan: str, billing_period: str) -> int:
        """Get plan amount in cents."""
        plans = {
            "starter": {"monthly": 1900, "yearly": 19000},
            "professional": {"monthly": 3900, "yearly": 39000},
            "power": {"monthly": 7900, "yearly": 79000},
        }
        return plans.get(plan, {}).get(billing_period, 0)
    
    def save_price_ids(self, price_ids: Dict[str, Dict[str, str]], output_file: str = ".env.paddle"):
        """
        Save price IDs to a file for easy configuration.
        
        Args:
            price_ids: Dictionary of price IDs
            output_file: Output file path
        """
        env_file = Path(__file__).parent.parent / output_file
        
        lines = [
            "# Paddle Price IDs - Generated by setup_paddle_products.py",
            "# Copy these to your .env file",
            "",
            "# Monthly Prices",
            f"PADDLE_STARTER_MONTHLY_PRICE_ID={price_ids.get('starter', {}).get('monthly_price_id', '')}",
            f"PADDLE_PROFESSIONAL_MONTHLY_PRICE_ID={price_ids.get('professional', {}).get('monthly_price_id', '')}",
            f"PADDLE_POWER_MONTHLY_PRICE_ID={price_ids.get('power', {}).get('monthly_price_id', '')}",
            "",
            "# Yearly Prices",
            f"PADDLE_STARTER_YEARLY_PRICE_ID={price_ids.get('starter', {}).get('yearly_price_id', '')}",
            f"PADDLE_PROFESSIONAL_YEARLY_PRICE_ID={price_ids.get('professional', {}).get('yearly_price_id', '')}",
            f"PADDLE_POWER_YEARLY_PRICE_ID={price_ids.get('power', {}).get('yearly_price_id', '')}",
            "",
            "# Product IDs (for reference)",
            f"PADDLE_STARTER_PRODUCT_ID={price_ids.get('starter', {}).get('product_id', '')}",
            f"PADDLE_PROFESSIONAL_PRODUCT_ID={price_ids.get('professional', {}).get('product_id', '')}",
            f"PADDLE_POWER_PRODUCT_ID={price_ids.get('power', {}).get('product_id', '')}",
        ]
        
        with open(env_file, "w") as f:
            f.write("\n".join(lines))
        
        print(f"\n✅ Price IDs saved to: {env_file}")
        print("\n📋 Copy these to your .env file:\n")
        print("\n".join(lines))
        print("\n")


def main():
    """Main function to run the setup."""
    try:
        setup = PaddleSetup()
        
        # Setup all products and prices
        price_ids = setup.setup_all_products()
        
        # Save price IDs to database
        print("\n💾 Saving prices to database...")
        db = SessionLocal()
        try:
            for plan_key, ids in price_ids.items():
                # Save monthly price
                if ids.get('monthly_price_id'):
                    PriceService.create_or_update_price(
                        plan=plan_key,
                        billing_period="monthly",
                        paddle_price_id=ids['monthly_price_id'],
                        amount=setup.get_plan_amount(plan_key, "monthly"),
                        paddle_product_id=ids.get('product_id'),
                        db=db
                    )
                    print(f"   ✅ Saved {plan_key} monthly price to database")
                
                # Save yearly price
                if ids.get('yearly_price_id'):
                    PriceService.create_or_update_price(
                        plan=plan_key,
                        billing_period="yearly",
                        paddle_price_id=ids['yearly_price_id'],
                        amount=setup.get_plan_amount(plan_key, "yearly"),
                        paddle_product_id=ids.get('product_id'),
                        db=db
                    )
                    print(f"   ✅ Saved {plan_key} yearly price to database")
        finally:
            db.close()
        
        # Also save to .env.paddle for reference
        setup.save_price_ids(price_ids)
        
        # Print summary
        print("\n" + "="*60)
        print("✅ Setup Complete!")
        print("="*60)
        print("\n📊 Summary:")
        for plan_key, ids in price_ids.items():
            print(f"\n  {plan_key.upper()}:")
            print(f"    Product ID: {ids.get('product_id')}")
            print(f"    Monthly Price ID: {ids.get('monthly_price_id')}")
            print(f"    Yearly Price ID: {ids.get('yearly_price_id')}")
        
        print("\n💡 Next Steps:")
        print("  1. Copy the price IDs from .env.paddle to your .env file (optional)")
        print("  2. Prices are already saved to the database")
        print("  3. Restart your backend service")
        print("\n")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
