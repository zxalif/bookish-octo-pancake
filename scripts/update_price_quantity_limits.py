#!/usr/bin/env python3
"""
Update Price Quantity Limits Script

This script updates existing prices in Paddle to add quantity limits (min: 1, max: 1).
This prevents customers from changing quantity in checkout.

Usage:
    python scripts/update_price_quantity_limits.py

Environment Variables Required:
    - PADDLE_API_KEY: Your Paddle API key
    - PADDLE_ENVIRONMENT: 'sandbox' or 'live' (default: 'sandbox')
"""

import os
import sys
from pathlib import Path
from typing import Dict, Optional

# Add parent directory to path to import core modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from paddle_billing import Client, Environment, Options
from paddle_billing.Resources.Prices.Operations import UpdatePrice
from paddle_billing.Exceptions.ApiError import ApiError

from core.config import get_settings
from core.database import SessionLocal
from services.price_service import PriceService

settings = get_settings()


def update_price_quantity_limits():
    """
    Update all existing prices in Paddle to add quantity limits (min: 1, max: 1).
    """
    print(f"\n🔧 Updating price quantity limits in {settings.PADDLE_ENVIRONMENT.upper()} environment...\n")
    
    # Initialize Paddle client
    paddle_env = settings.PADDLE_ENVIRONMENT.lower()
    if paddle_env == "sandbox" or settings.ENVIRONMENT == "development":
        options = Options(environment=Environment.SANDBOX)
        paddle = Client(settings.PADDLE_API_KEY, options=options)
        print(f"✅ Paddle client initialized with SANDBOX environment")
    else:
        options = Options(environment=Environment.PRODUCTION)
        paddle = Client(settings.PADDLE_API_KEY, options=options)
        print(f"✅ Paddle client initialized with PRODUCTION environment")
    
    # Get all active prices from database
    db = SessionLocal()
    try:
        prices = PriceService.get_all_active_prices(db)
        
        if not prices:
            print("⚠️  No active prices found in database.")
            return
        
        print(f"📋 Found {len(prices)} active prices to update\n")
        
        updated_count = 0
        failed_count = 0
        
        for price in prices:
            try:
                print(f"🔄 Updating price: {price.plan} ({price.billing_period.value}) - {price.paddle_price_id}")
                
                # Update price with quantity limits
                # Note: Paddle SDK UpdatePrice may require different structure
                # If this fails, we may need to check the SDK documentation
                updated_price = paddle.prices.update(
                    price.paddle_price_id,
                    UpdatePrice(
                        quantity={
                            "minimum": 1,
                            "maximum": 1
                        }
                    )
                )
                
                print(f"   ✅ Updated successfully")
                updated_count += 1
                
            except ApiError as e:
                print(f"   ❌ Failed to update: {e.error_code} - {e}")
                failed_count += 1
            except Exception as e:
                print(f"   ❌ Unexpected error: {str(e)}")
                failed_count += 1
        
        print(f"\n📊 Summary:")
        print(f"   ✅ Successfully updated: {updated_count}")
        print(f"   ❌ Failed: {failed_count}")
        
        if failed_count > 0:
            print(f"\n⚠️  Some prices failed to update. You may need to:")
            print(f"   1. Update them manually in Paddle Dashboard")
            print(f"   2. Or delete and recreate them using setup_paddle_products.py")
        
    finally:
        db.close()


if __name__ == "__main__":
    try:
        update_price_quantity_limits()
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        sys.exit(1)

