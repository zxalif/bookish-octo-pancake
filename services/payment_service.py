"""
Payment Service

Handles Paddle payment integration business logic:
- Create checkout sessions
- Handle webhook events
- Process payments
- Link payments to subscriptions
"""

from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
import httpx
import hmac
import hashlib
import json

from models.user import User
from models.subscription import Subscription, SubscriptionStatus, SubscriptionPlan
from models.payment import Payment, PaymentStatus
from models.price import BillingPeriod
from services.subscription_service import SubscriptionService
from services.price_service import PriceService
from core.config import get_settings
from core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class PaymentService:
    """Service for handling payment operations with Paddle."""
    
    @staticmethod
    def get_paddle_api_url() -> str:
        """
        Get Paddle API base URL based on environment.
        
        Returns:
            str: Paddle API base URL
        """
        if settings.PADDLE_ENVIRONMENT == "sandbox":
            return "https://sandbox-api.paddle.com"
        return "https://api.paddle.com"
    
    @staticmethod
    def get_paddle_checkout_url() -> str:
        """
        Get Paddle Checkout URL based on environment.
        
        Returns:
            str: Paddle Checkout URL
        """
        if settings.PADDLE_ENVIRONMENT == "sandbox":
            return "https://sandbox-checkout.paddle.com"
        return "https://checkout.paddle.com"
    
    @staticmethod
    async def create_checkout_session(
        user_id: str,
        plan: str,
        billing_period: str = "monthly",  # "monthly" or "yearly"
        db: Session = None
    ) -> Dict[str, Any]:
        """
        Create a Paddle checkout session for subscription.
        
        Args:
            user_id: User UUID
            plan: Subscription plan (starter, professional, power)
            billing_period: Billing period - "monthly" or "yearly" (default: "monthly")
            db: Database session
            
        Returns:
            dict: Checkout session data with checkout_url
            
        Raises:
            ValueError: If plan is invalid
            HTTPException: If Paddle API call fails
        """
        # Validate plan
        try:
            plan_enum = SubscriptionPlan(plan)
        except ValueError:
            raise ValueError(f"Invalid plan: {plan}")
        
        # Get user
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Get price from database (single source of truth)
        billing_period = billing_period.lower()
        if billing_period not in ["monthly", "yearly"]:
            billing_period = "monthly"  # Default to monthly
        
        # Get price from database (single source of truth)
        # Prices are stored in the database, not in environment variables
        price = PriceService.get_price_by_plan_and_period(plan, billing_period, db)
        
        if not price:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "message": f"Price not found in database for plan: {plan}, billing_period: {billing_period}",
                    "error_code": "price_not_found",
                    "troubleshooting": (
                        "Prices must be stored in the database. Please run the setup script:\n"
                        "python scripts/setup_paddle_products.py\n\n"
                        "This will create Paddle products and save price IDs to the database."
                    )
                }
            )
        
        price_id = price.paddle_price_id
        
        # Create checkout session via Paddle Transactions API
        # 
        # IMPORTANT: We use Transactions API, not Subscriptions API
        # - Subscriptions API cannot create checkout sessions
        # - Subscriptions API cannot create subscriptions directly
        # - Paddle automatically creates subscriptions when customers pay for recurring items
        # 
        # Flow: Create transaction → Customer pays → Paddle creates subscription automatically
        # Reference: https://developer.paddle.com/api-reference/subscriptions/overview
        #
        # Note: This requires a default checkout URL set in Paddle dashboard
        api_url = PaymentService.get_paddle_api_url()
        url = f"{api_url}/transactions"
        
        headers = {
            "Authorization": f"Bearer {settings.PADDLE_API_KEY}",
            "Content-Type": "application/json"
        }
        
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:9100')
        
        # Paddle Transactions API payload
        # Note: The default checkout URL must be set in Paddle dashboard
        # return_url: Where to redirect after successful payment
        # cancel_url: Where to redirect if user cancels (optional, uses return_url if not provided)
        payload = {
            "items": [
                {
                    "price_id": price_id,
                    "quantity": 1
                }
            ],
            "customer_email": user.email,
            "return_url": f"{frontend_url}/dashboard/subscription/success",
            "cancel_url": f"{frontend_url}/dashboard/subscription/cancel",
            "custom_data": {
                "user_id": user_id,
                "plan": plan
            }
            # Note: We don't include checkout.url - Paddle uses the default checkout URL
            # from the dashboard. The checkout_url in the response is Paddle's hosted checkout.
        }
        
        # Add customer_id only if it exists (Paddle doesn't accept null)
        if user.paddle_customer_id:
            payload["customer_id"] = user.paddle_customer_id
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                
                # Check for errors before raising
                if response.status_code >= 400:
                    error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                    error_info = error_data.get("error", {})
                    
                    # Parse Paddle error response
                    error_code = error_info.get("code", "unknown_error")
                    error_detail = error_info.get("detail", "An error occurred with the payment provider")
                    error_type = error_info.get("type", "request_error")
                    
                    # Map common Paddle errors to user-friendly messages with troubleshooting
                    user_friendly_messages = {
                        "transaction_default_checkout_url_not_set": (
                            f"Payment system configuration error.\n\n"
                            f"Please verify:\n"
                            f"1. Default checkout URL is set in Paddle dashboard ({settings.PADDLE_ENVIRONMENT.upper()} environment)\n"
                            f"2. Go to Checkout → Checkout Settings → Default Payment Link\n"
                            f"3. Your website is approved in Paddle dashboard (Checkout → Website Approval)\n"
                            f"4. The checkout URL includes Paddle.js integration\n"
                            f"\nCurrent environment: {settings.PADDLE_ENVIRONMENT.upper()}\n"
                            f"Dashboard URL: {'https://sandbox-vendors.paddle.com' if settings.PADDLE_ENVIRONMENT == 'sandbox' else 'https://vendors.paddle.com'}\n"
                            f"If you've configured it, make sure you're in the correct environment dashboard."
                        ),
                        "price_not_found": "The selected pricing plan is not available. Please try again or contact support.",
                        "invalid_price": "Invalid pricing configuration. Please contact support.",
                        "customer_not_found": "Customer account issue. Please contact support.",
                        "insufficient_permissions": "Payment system permissions error. Please contact support.",
                    }
                    
                    # Get user-friendly message or use Paddle's detail
                    user_message = user_friendly_messages.get(error_code, error_detail)
                    
                    # Log the full error for debugging
                    from core.logger import get_logger
                    logger = get_logger(__name__)
                    logger.error(
                        f"Paddle API error: code={error_code}, type={error_type}, detail={error_detail}, "
                        f"status={response.status_code}, environment={settings.PADDLE_ENVIRONMENT}, "
                        f"request_id={error_data.get('meta', {}).get('request_id')}, "
                        f"payload_checkout_url={payload.get('checkout', {}).get('url')}"
                    )
                    
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail={
                            "message": user_message,
                            "error_code": error_code,
                            "error_type": error_type,
                            "paddle_error": error_detail,
                            "documentation_url": error_info.get("documentation_url"),
                            "request_id": error_data.get("meta", {}).get("request_id"),
                            "environment": settings.PADDLE_ENVIRONMENT,
                            "troubleshooting": (
                                f"Make sure you're configuring the checkout URL in the correct environment dashboard "
                                f"({settings.PADDLE_ENVIRONMENT.upper()}) that matches your PADDLE_ENVIRONMENT setting. "
                                f"Dashboard: {'https://sandbox-vendors.paddle.com' if settings.PADDLE_ENVIRONMENT == 'sandbox' else 'https://vendors.paddle.com'}"
                            ) if error_code == "transaction_default_checkout_url_not_set" else None
                        }
                    )
                
                response.raise_for_status()
                data = response.json()
                
                # Extract transaction ID (from data.id or id)
                response_data = data.get("data", data)  # Handle both wrapped and unwrapped responses
                transaction_id = response_data.get("id") or data.get("id")
                
                if not transaction_id:
                    logger.error(f"Paddle API response missing transaction_id. Response: {data}")
                    
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail={
                            "message": "Failed to get transaction ID from payment provider",
                            "error_code": "transaction_id_missing",
                            "paddle_response": str(data)[:500]
                        }
                    )
                
                # Extract customer ID
                customer_id = response_data.get("customer_id") or data.get("customer_id")
                
                # Use Paddle's hosted checkout URL directly (avoids CSP issues with localhost)
                # Paddle returns checkout.url in the response which points to their hosted checkout page
                # This is better than embedding because:
                # 1. No CSP issues (no iframe needed)
                # 2. Works with localhost (HTTP or HTTPS)
                # 3. Simpler implementation
                checkout_url = (
                    response_data.get("checkout", {}).get("url") or  # Transactions API format: data.checkout.url
                    data.get("checkout", {}).get("url") or           # Alternative: checkout.url (direct)
                    data.get("checkout_url")                         # Fallback: checkout_url (direct)
                )
                
                # If Paddle doesn't return a checkout URL, fall back to our checkout page
                # (This should rarely happen if default payment link is set correctly)
                if not checkout_url:
                    logger.warning(
                        f"Paddle API response missing checkout.url, using fallback. "
                        f"Response: {str(data)[:500]}"
                    )
                    # Fallback: Use our checkout page (requires Paddle.js)
                    checkout_url = f"{frontend_url}/checkout?_ptxn={transaction_id}"
                
                # Log for debugging
                logger.info(
                    f"Paddle checkout created: transaction_id={transaction_id}, "
                    f"checkout_url={checkout_url}, using_hosted={checkout_url.startswith('https://')}"
                )
                
                return {
                    "checkout_url": checkout_url,
                    "transaction_id": transaction_id,
                    "customer_id": customer_id
                }
        except HTTPException:
            # Re-raise HTTPExceptions (already formatted)
            raise
        except httpx.HTTPStatusError as e:
            # Fallback for non-JSON error responses
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": "Payment provider error. Please try again or contact support.",
                    "error_code": "paddle_api_error",
                    "raw_error": e.response.text[:500] if hasattr(e.response, 'text') else str(e)
                }
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "Unable to connect to payment provider. Please try again later.",
                    "error_code": "paddle_connection_error"
                }
            )
        except Exception as e:
            logger.error(f"Unexpected error in create_checkout_session: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "message": "An unexpected error occurred. Please try again or contact support.",
                    "error_code": "internal_error"
                }
            )
    
    @staticmethod
    def verify_webhook_signature(
        payload: bytes,
        signature: str
    ) -> bool:
        """
        Verify Paddle webhook signature.
        
        Args:
            payload: Raw webhook payload
            signature: Webhook signature from header
            
        Returns:
            bool: True if signature is valid
        """
        if not settings.PADDLE_WEBHOOK_SECRET:
            # In development, you might skip verification
            return True
        
        expected_signature = hmac.new(
            settings.PADDLE_WEBHOOK_SECRET.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_signature, signature)
    
    @staticmethod
    async def handle_webhook_event(
        event_data: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """
        Handle Paddle webhook event.
        
        Supported events:
        - transaction.completed
        - transaction.payment_failed
        - subscription.created
        - subscription.updated
        - subscription.canceled
        
        Args:
            event_data: Webhook event data from Paddle
            db: Database session
            
        Returns:
            dict: Processing result
        """
        event_type = event_data.get("event_type")
        data = event_data.get("data", {})
        
        if event_type == "transaction.completed":
            return await PaymentService._handle_transaction_completed(data, db)
        elif event_type == "transaction.payment_failed":
            return await PaymentService._handle_transaction_failed(data, db)
        elif event_type == "subscription.created":
            return await PaymentService._handle_subscription_created(data, db)
        elif event_type == "subscription.updated":
            return await PaymentService._handle_subscription_updated(data, db)
        elif event_type == "subscription.canceled":
            return await PaymentService._handle_subscription_canceled(data, db)
        else:
            # Unknown event type - log but don't fail
            return {
                "status": "ignored",
                "message": f"Unknown event type: {event_type}"
            }
    
    @staticmethod
    async def _handle_transaction_completed(
        data: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Handle transaction.completed webhook event."""
        transaction_id = data.get("id")
        customer_id = data.get("customer_id")
        custom_data = data.get("custom_data", {})
        user_id = custom_data.get("user_id")
        plan = custom_data.get("plan")
        
        if not user_id or not plan:
            return {
                "status": "error",
                "message": "Missing user_id or plan in custom_data"
            }
        
        # Get or create user
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return {
                "status": "error",
                "message": f"User not found: {user_id}"
            }
        
        # Update user's Paddle customer ID if not set
        if customer_id and not user.paddle_customer_id:
            user.paddle_customer_id = customer_id
            db.commit()
        
        # Create or update payment record
        payment = db.query(Payment).filter(
            Payment.paddle_transaction_id == transaction_id
        ).first()
        
        if not payment:
            payment = Payment(
                user_id=user_id,
                amount=int(float(data.get("totals", {}).get("total", 0)) * 100),  # Convert to cents
                currency=data.get("currency_code", "USD"),
                status=PaymentStatus.COMPLETED,
                paddle_transaction_id=transaction_id,
                paddle_invoice_id=data.get("invoice_id"),
                payment_method=data.get("payment_method_type")
            )
            db.add(payment)
        else:
            payment.status = PaymentStatus.COMPLETED
            payment.amount = int(float(data.get("totals", {}).get("total", 0)) * 100)
        
        # Create or update subscription
        subscription = SubscriptionService.get_active_subscription(user_id, db)
        if not subscription:
            subscription = SubscriptionService.create_subscription(
                user_id=user_id,
                plan=plan,
                paddle_subscription_id=data.get("subscription_id"),
                db=db
            )
        
        # Link payment to subscription
        payment.subscription_id = subscription.id
        db.commit()
        
        return {
            "status": "success",
            "message": "Transaction processed successfully",
            "payment_id": payment.id,
            "subscription_id": subscription.id
        }
    
    @staticmethod
    async def _handle_transaction_failed(
        data: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Handle transaction.payment_failed webhook event."""
        transaction_id = data.get("id")
        
        payment = db.query(Payment).filter(
            Payment.paddle_transaction_id == transaction_id
        ).first()
        
        if payment:
            payment.status = PaymentStatus.FAILED
            db.commit()
        
        return {
            "status": "success",
            "message": "Payment failure recorded"
        }
    
    @staticmethod
    async def _handle_subscription_created(
        data: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Handle subscription.created webhook event."""
        subscription_id = data.get("id")
        customer_id = data.get("customer_id")
        
        # Find user by Paddle customer ID
        user = db.query(User).filter(User.paddle_customer_id == customer_id).first()
        if not user:
            return {
                "status": "error",
                "message": f"User not found for customer_id: {customer_id}"
            }
        
        # Get plan from subscription items
        items = data.get("items", [])
        if not items:
            return {
                "status": "error",
                "message": "No items in subscription"
            }
        
        # Map price_id to plan (you'll need to configure this)
        # For now, we'll try to get it from custom_data or infer from price
        plan = "professional"  # Default
        
        # Get price from database
        price = None
        if items and len(items) > 0:
            price_id = items[0].get("price_id")
            if price_id:
                price = PriceService.get_price_by_paddle_id(price_id, db)
        
        # Create subscription if it doesn't exist
        existing = SubscriptionService.get_active_subscription(user.id, db)
        if not existing:
            SubscriptionService.create_subscription(
                user_id=user.id,
                plan=plan,
                billing_period=billing_period,
                paddle_subscription_id=subscription_id,
                price_id=price.id if price else None,
                db=db
            )
        
        return {
            "status": "success",
            "message": "Subscription created"
        }
    
    @staticmethod
    async def _handle_subscription_updated(
        data: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Handle subscription.updated webhook event."""
        subscription_id = data.get("id")
        status_str = data.get("status")
        
        # Find subscription by Paddle ID
        subscription = db.query(Subscription).filter(
            Subscription.paddle_subscription_id == subscription_id
        ).first()
        
        if subscription:
            # Map Paddle status to our status
            status_map = {
                "active": SubscriptionStatus.ACTIVE,
                "canceled": SubscriptionStatus.CANCELLED,
                "past_due": SubscriptionStatus.PAST_DUE,
                "trialing": SubscriptionStatus.TRIALING
            }
            
            if status_str in status_map:
                subscription.status = status_map[status_str]
                db.commit()
        
        return {
            "status": "success",
            "message": "Subscription updated"
        }
    
    @staticmethod
    async def _handle_subscription_canceled(
        data: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Handle subscription.canceled webhook event."""
        subscription_id = data.get("id")
        
        subscription = db.query(Subscription).filter(
            Subscription.paddle_subscription_id == subscription_id
        ).first()
        
        if subscription:
            subscription.status = SubscriptionStatus.CANCELLED
            db.commit()
        
        return {
            "status": "success",
            "message": "Subscription canceled"
        }

