#!/usr/bin/env python3
"""
Paddle Products Setup Script

This script creates products and prices in Paddle for development/testing.
It creates:
- 3 Products: Starter, Professional, Power
- 2 Prices per product: Monthly and Yearly

Usage:
    python scripts/setup_paddle_products.py

Environment Variables Required:
    - PADDLE_API_KEY: Your Paddle API key
    - PADDLE_ENVIRONMENT: 'sandbox' or 'live' (default: 'sandbox')
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional
import httpx

# Add parent directory to path to import core modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import get_settings
from core.database import SessionLocal
from services.price_service import PriceService

settings = get_settings()


class PaddleSetup:
    """Helper class for setting up Paddle products and prices."""
    
    def __init__(self):
        self.api_key = settings.PADDLE_API_KEY
        self.environment = settings.PADDLE_ENVIRONMENT or "sandbox"
        
        if not self.api_key:
            raise ValueError("PADDLE_API_KEY environment variable is required")
        
        # Set base URL based on environment
        if self.environment == "sandbox":
            self.base_url = "https://sandbox-api.paddle.com"
        else:
            self.base_url = "https://api.paddle.com"
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Paddle-Version": "1"  # Paddle API version
        }
    
    async def get_vendor_id(self) -> Optional[str]:
        """
        Get vendor ID from Paddle account.
        
        Returns:
            str: Vendor ID if found, None otherwise
        """
        try:
            # Try to get vendor info from a simple API call
            # Note: Paddle API might not have a direct vendor endpoint
            # We'll try to get it from the first product or transaction
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get products to extract vendor info
                response = await client.get(
                    f"{self.base_url}/products",
                    headers=self.headers
                )
                response.raise_for_status()
                data = response.json()
                
                # Vendor ID might be in the response or we can use settings
                if settings.PADDLE_VENDOR_ID:
                    return settings.PADDLE_VENDOR_ID
                
                # Try to extract from response
                # Note: This is a workaround - vendor ID is usually in dashboard
                print("⚠️  Vendor ID not found in API response.")
                print("   Please get it from Paddle Dashboard > Developer Tools")
                return None
        except Exception as e:
            print(f"⚠️  Could not fetch vendor ID: {e}")
            if settings.PADDLE_VENDOR_ID:
                return settings.PADDLE_VENDOR_ID
            return None
    
    async def create_product(
        self,
        name: str,
        description: str,
        product_type: str = "standard"
    ) -> Dict[str, Any]:
        """
        Create a product in Paddle.
        
        Args:
            name: Product name
            description: Product description
            product_type: Product type (default: "standard")
            
        Returns:
            dict: Created product data
        """
        url = f"{self.base_url}/products"
        
        payload = {
            "name": name,
            "description": description,
            "type": product_type,
            "tax_category": "standard"  # Standard tax category
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                
                product = data.get("data", {})
                print(f"✅ Created product: {name} (ID: {product.get('id')})")
                return product
        except httpx.HTTPStatusError as e:
            error_data = e.response.json() if e.response.content else {}
            error_detail = error_data.get("detail", str(e))
            
            # Check if product already exists
            if "already exists" in str(error_detail).lower() or e.response.status_code == 409:
                print(f"⚠️  Product '{name}' might already exist. Checking existing products...")
                # Try to find existing product
                existing = await self.find_product_by_name(name)
                if existing:
                    print(f"✅ Found existing product: {name} (ID: {existing.get('id')})")
                    return existing
            
            raise Exception(f"Failed to create product '{name}': {error_detail}")
    
    async def find_product_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find a product by name."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/products",
                    headers=self.headers,
                    params={"per_page": 100}
                )
                response.raise_for_status()
                data = response.json()
                
                products = data.get("data", [])
                for product in products:
                    if product.get("name") == name:
                        return product
                return None
        except Exception as e:
            print(f"⚠️  Error finding product: {e}")
            return None
    
    async def create_price(
        self,
        product_id: str,
        description: str,
        amount: int,  # Amount in cents
        currency: str = "USD",
        billing_cycle: str = "month",  # "month" or "year"
        interval: int = 1
    ) -> Dict[str, Any]:
        """
        Create a price for a product.
        
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
        url = f"{self.base_url}/prices"
        
        # Paddle API billing cycle structure
        # Based on Paddle API docs: billing_cycle should be an object with interval and frequency
        billing_cycle_obj = {
            "interval": billing_cycle,  # "month" or "year"
            "frequency": interval  # How many intervals (1 = every month/year)
        }
        
        payload = {
            "product_id": product_id,
            "description": description,
            "type": "standard",  # Standard price type
            "billing_cycle": billing_cycle_obj,
            "trial_period": None,  # No trial period
            "tax_mode": "external",  # Tax handled externally
            "unit_price": {
                "amount": str(amount),
                "currency_code": currency
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                
                price = data.get("data", {})
                print(f"   ✅ Created price: {description} (ID: {price.get('id')}) - ${amount/100:.2f}/{billing_cycle}")
                return price
        except httpx.HTTPStatusError as e:
            error_data = e.response.json() if e.response.content else {}
            error_detail = error_data.get("detail", str(e))
            raise Exception(f"Failed to create price '{description}': {error_detail}")
    
    async def setup_all_products(self) -> Dict[str, Dict[str, str]]:
        """
        Create all products and prices for ClientHunt.
        
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
            product = await self.create_product(
                name=f"ClientHunt {plan_data['name']}",
                description=plan_data['description']
            )
            product_id = product.get("id")
            
            if not product_id:
                print(f"❌ Failed to get product ID for {plan_data['name']}")
                continue
            
            # Create monthly price
            monthly_price = await self.create_price(
                product_id=product_id,
                description=f"{plan_data['name']} - Monthly",
                amount=plan_data['monthly_price'],
                billing_cycle="month",
                interval=1
            )
            
            # Create yearly price
            yearly_price = await self.create_price(
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


async def main():
    """Main function to run the setup."""
    try:
        setup = PaddleSetup()
        
        # Get vendor ID
        print("🔍 Getting vendor ID...")
        vendor_id = await setup.get_vendor_id()
        if vendor_id:
            print(f"✅ Vendor ID: {vendor_id}")
        else:
            print("⚠️  Vendor ID not found. Please set PADDLE_VENDOR_ID in .env")
        
        # Setup all products and prices
        price_ids = await setup.setup_all_products()
        
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
        print("  1. Copy the price IDs from .env.paddle to your .env file")
        print("  2. Update your .env with PADDLE_VENDOR_ID if not set")
        print("  3. Restart your backend service")
        print("\n")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

