"""
Payment Service

Handles Paddle payment integration business logic using the official Paddle Python SDK:
- Create checkout sessions
- Handle webhook events
- Process payments
- Link payments to subscriptions
"""

from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException, status, Request
from paddle_billing import Client, Environment, Options
from paddle_billing.Resources.Transactions.Operations import CreateTransaction, UpdateTransaction
from paddle_billing.Resources.Subscriptions.Operations import UpdateSubscription
from paddle_billing.Exceptions.ApiError import ApiError
from paddle_billing.Notifications import Verifier, Secret

from models.user import User
from models.subscription import Subscription, SubscriptionStatus, SubscriptionPlan
from models.payment import Payment, PaymentStatus
from models.user_audit_log import UserAuditLog
from models.price import BillingPeriod
from services.subscription_service import SubscriptionService
from services.price_service import PriceService
from services.email_service import EmailService
from core.config import get_settings
from core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class PaymentService:
    """Service for handling payment operations with Paddle using the official SDK."""
    
    # Initialize Paddle SDK client (singleton pattern)
    _paddle_client: Optional[Client] = None
    
    @classmethod
    def is_paddle_enabled(cls) -> bool:
        """
        Check if Paddle payment gateway is enabled.
        
        Returns:
            bool: True if Paddle is enabled, False otherwise
        """
        return settings.PADDLE_ENABLED and bool(settings.PADDLE_API_KEY)
    
    @classmethod
    def get_paddle_client(cls) -> Client:
        """
        Get or create Paddle SDK client instance.
        
        Automatically uses sandbox environment in development or when PADDLE_ENVIRONMENT=sandbox.
        
        Returns:
            Client: Paddle SDK client
            
        Raises:
            ValueError: If Paddle is disabled or API key is not configured
        """
        if not cls.is_paddle_enabled():
            raise ValueError(
                "Paddle payment gateway is disabled. "
                "Set PADDLE_ENABLED=true and PADDLE_API_KEY to enable it."
            )
        
        if cls._paddle_client is None:
            if not settings.PADDLE_API_KEY:
                raise ValueError("PADDLE_API_KEY environment variable is required")
            
            # Determine environment - use sandbox in development or when explicitly set
            paddle_env = settings.PADDLE_ENVIRONMENT.lower()
            if paddle_env == "sandbox" or settings.ENVIRONMENT == "development":
                # Use sandbox environment
                options = Options(environment=Environment.SANDBOX)
                cls._paddle_client = Client(settings.PADDLE_API_KEY, options=options)
                logger.info("Paddle client initialized with SANDBOX environment")
            else:
                # Use production environment
                options = Options(environment=Environment.PRODUCTION)
                cls._paddle_client = Client(settings.PADDLE_API_KEY, options=options)
                logger.info("Paddle client initialized with PRODUCTION environment")
        
        return cls._paddle_client
    
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
        Create a Paddle checkout session for subscription using the SDK.
        
        This method follows Paddle's recommended workflow:
        1. Creates a transaction with recurring items (price_id + quantity)
        2. Prefills customer email if no customer_id exists
        3. Extracts checkout URL from transaction response
        4. Paddle automatically creates subscription when payment completes
        
        References:
        - Transaction creation: https://developer.paddle.com/build/transactions/create-a-transaction
        - Subscription creation: https://developer.paddle.com/build/lifecycle/subscription-creation
        - Default payment link: https://developer.paddle.com/build/checkout/set-your-default-payment-link
        
        Args:
            user_id: User UUID
            plan: Subscription plan (starter, professional, power)
            billing_period: Billing period - "monthly" or "yearly" (default: "monthly")
            db: Database session
            
        Returns:
            dict: Checkout session data with checkout_url, transaction_id, and customer_id
            
        Raises:
            ValueError: If plan is invalid
            HTTPException: If Paddle API call fails
            
        Note:
            - Return URL must be configured in Paddle dashboard settings
            - Paddle automatically creates subscriptions when recurring items are paid
            - Customer email is prefilled to improve checkout UX
        """
        # Check if Paddle is enabled
        if not PaymentService.is_paddle_enabled():
            raise ValueError(
                "Paddle payment gateway is disabled. "
                "Set PADDLE_ENABLED=true and PADDLE_API_KEY to enable it."
            )
        
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
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:9100')
        
        # Create checkout session via Paddle SDK
        # Paddle automatically creates subscriptions when customers pay for recurring items
        try:
            paddle = PaymentService.get_paddle_client()
            
            # Build transaction items
            # According to Paddle SDK, items should be a list of TransactionItem objects
            # But we'll try dictionaries first (similar to CreatePrice pattern)
            # If that fails, we'll need to import TransactionItem class
            # 
            # Note: The quantity field is required by Paddle and is used for:
            # - Per-seat billing (e.g., team plans where you buy multiple seats)
            # - Multiple units of a subscription
            # For single subscriptions, quantity: 1 is correct and standard
            # The checkout UI will show "Quantity 1" which is normal Paddle behavior
            transaction_items = [
                {
                    "price_id": price_id,
                    "quantity": 1  # Standard for single subscriptions; can be increased for per-seat billing
                }
            ]
            
            # Build CreateTransaction parameters
            # According to Paddle SDK documentation:
            # - items (required): List of transaction items with price_id and quantity
            # - customer_id (optional): Existing Paddle customer ID
            # - customer (optional): Customer object with email to prefill checkout
            # - custom_data (optional): Metadata to store with transaction
            # - status (optional): Set to "billed" to lock transaction and prevent quantity changes
            # - return_url: Configured in Paddle dashboard settings, not in transaction creation
            # Reference: https://developer.paddle.com/build/transactions/create-a-transaction
            # Reference: https://developer.paddle.com/build/checkout/pass-update-checkout-items
            create_params = {
                "items": transaction_items,
                "custom_data": {
                    "user_id": user_id,
                    "plan": plan,
                    "user_email": user.email,  # Store email in custom_data for reference
                    "return_url": f"{frontend_url}/dashboard/subscription/success"  # Store in custom_data for reference
                },
            }
            
            # Note: We don't create transactions as "billed" initially because:
            # 1. Billed transactions are for manual collection, not automatic payment
            # 2. For subscriptions, we need automatic payment collection
            # Instead, we use the eventCallback approach in the frontend to mark the transaction
            # as billed once it's ready, which locks quantity while still allowing payment
            # Reference: https://developer.paddle.com/build/checkout/pass-update-checkout-items
            
            # Add customer_id if available
            # Note: The Paddle Python SDK's CreateTransaction doesn't support passing
            # customer email directly. Customer email prefilling should be done via
            # Paddle.js on the frontend when opening the checkout overlay.
            # Reference: https://developer.paddle.com/build/transactions/create-a-transaction
            if user.paddle_customer_id:
                create_params["customer_id"] = user.paddle_customer_id
            # If no customer_id exists, Paddle will collect customer email during checkout
            # The frontend can prefill the email using Paddle.js Checkout.open() with customer.email
            
            # Create transaction using SDK
            # Paddle automatically creates subscriptions when customers pay for recurring items
            # Reference: https://developer.paddle.com/build/lifecycle/subscription-creation
            # The checkout URL is returned in the transaction response
            transaction = paddle.transactions.create(CreateTransaction(**create_params))
            
            # Extract transaction ID
            transaction_id = transaction.id
            
            # Extract customer ID if available
            customer_id = transaction.customer_id if hasattr(transaction, 'customer_id') else None
            
            # Update user's Paddle customer ID if not set
            if customer_id and not user.paddle_customer_id:
                user.paddle_customer_id = customer_id
                db.commit()
            
            # Get checkout URL from transaction
            # Paddle returns checkout.url in the transaction response
            # Reference: https://developer.paddle.com/build/transactions/create-a-transaction
            checkout_url = None
            if hasattr(transaction, 'checkout') and transaction.checkout:
                checkout_url = transaction.checkout.url if hasattr(transaction.checkout, 'url') else None
            
            # Fallback: Use default payment link pattern
            # If checkout.url is not available, use the default payment link format
            # Reference: https://developer.paddle.com/build/checkout/set-your-default-payment-link
            # Format: your-default-payment-link?_ptxn=transaction_id
            if not checkout_url:
                logger.warning(
                    f"Paddle transaction missing checkout.url, using fallback pattern. "
                    f"Transaction ID: {transaction_id}. "
                    f"Make sure default payment link is configured in Paddle dashboard."
                )
                # Fallback: Use default payment link pattern with transaction ID
                # This requires Paddle.js to be loaded on the frontend
                checkout_url = f"{frontend_url}/checkout?_ptxn={transaction_id}"
            
            # Log for debugging
            logger.info(
                f"Paddle checkout created: transaction_id={transaction_id}, "
                f"checkout_url={checkout_url}, using_hosted={checkout_url.startswith('https://')}"
            )
            
            return {
                "checkout_url": checkout_url,
                "transaction_id": transaction_id,
                "customer_id": customer_id,
                "price_id": price_id  # Include price_id so frontend can lock quantity
            }
            
        except ApiError as e:
            # Handle Paddle API errors
            error_code = e.error_code if hasattr(e, 'error_code') else "unknown_error"
            error_detail = str(e)
            
            # Map common Paddle errors to user-friendly messages
            user_friendly_messages = {
                "transaction_default_checkout_url_not_set": (
                    f"Payment system configuration error.\n\n"
                    f"Please verify:\n"
                    f"1. Default checkout URL is set in Paddle dashboard ({settings.PADDLE_ENVIRONMENT.upper()} environment)\n"
                    f"2. Go to Checkout → Checkout Settings → Default Payment Link\n"
                    f"3. Your website is approved in Paddle dashboard (Checkout → Website Approval)\n"
                    f"\nCurrent environment: {settings.PADDLE_ENVIRONMENT.upper()}\n"
                    f"Dashboard URL: {'https://sandbox-vendors.paddle.com' if settings.PADDLE_ENVIRONMENT == 'sandbox' else 'https://vendors.paddle.com'}\n"
                ),
                "price_not_found": "The selected pricing plan is not available. Please try again or contact support.",
                "invalid_price": "Invalid pricing configuration. Please contact support.",
                "customer_not_found": "Customer account issue. Please contact support.",
                "insufficient_permissions": "Payment system permissions error. Please contact support.",
            }
            
            user_message = user_friendly_messages.get(error_code, error_detail)
            
            logger.error(
                f"Paddle API error: code={error_code}, detail={error_detail}, "
                f"environment={settings.PADDLE_ENVIRONMENT}"
            )
            
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": user_message,
                    "error_code": error_code,
                    "paddle_error": error_detail,
                    "environment": settings.PADDLE_ENVIRONMENT,
                    "troubleshooting": (
                        f"Make sure you're configuring the checkout URL in the correct environment dashboard "
                        f"({settings.PADDLE_ENVIRONMENT.upper()}) that matches your PADDLE_ENVIRONMENT setting. "
                        f"Dashboard: {'https://sandbox-vendors.paddle.com' if settings.PADDLE_ENVIRONMENT == 'sandbox' else 'https://vendors.paddle.com'}"
                    ) if error_code == "transaction_default_checkout_url_not_set" else None
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
        request: Request,
        signature: str
    ) -> bool:
        """
        Verify Paddle webhook signature using the SDK.
        
        Args:
            request: FastAPI Request object
            signature: Webhook signature from header
            
        Returns:
            bool: True if signature is valid
        """
        if not settings.PADDLE_WEBHOOK_SECRET:
            # In development, you might skip verification
            logger.warning("PADDLE_WEBHOOK_SECRET not set - skipping webhook verification")
            return True
        
        try:
            # Use Paddle SDK's webhook verifier
            verifier = Verifier()
            secret = Secret(settings.PADDLE_WEBHOOK_SECRET)
            
            # The SDK's verify method accepts request objects that match the Request protocol
            # FastAPI Request should work, but we may need to adapt it
            integrity_check = verifier.verify(request, secret)
            
            return integrity_check
        except Exception as e:
            logger.error(f"Error verifying webhook signature: {str(e)}")
            return False
    
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
        user_id = custom_data.get("user_id") if isinstance(custom_data, dict) else None
        plan = custom_data.get("plan", "professional") if isinstance(custom_data, dict) else "professional"
        billing_period = custom_data.get("billing_period", "monthly") if isinstance(custom_data, dict) else "monthly"
        
        # If user_id not in custom_data, try to find by customer_id
        if not user_id and customer_id:
            user = db.query(User).filter(User.paddle_customer_id == customer_id).first()
            if user:
                user_id = user.id
        
        if not user_id:
            return {
                "status": "error",
                "message": f"User not found for transaction {transaction_id}"
            }
        
        # Get user
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
        
        # Get price from transaction items to ensure we have correct plan/billing_period
        price = None
        items = data.get("items", [])
        if items and len(items) > 0:
            first_item = items[0] if isinstance(items, list) else items
            price_id = None
            if isinstance(first_item, dict):
                price_id = first_item.get("price_id") or (first_item.get("price", {}) or {}).get("id")
            elif hasattr(first_item, 'price_id'):
                price_id = first_item.price_id
            elif hasattr(first_item, 'price') and hasattr(first_item.price, 'id'):
                price_id = first_item.price.id
            
            if price_id:
                price = PriceService.get_price_by_paddle_id(price_id, db)
                if price:
                    # Override with price from database (single source of truth)
                    plan = price.plan
                    billing_period = price.billing_period.value if price.billing_period else "monthly"
        
        # Create or update payment record
        payment = db.query(Payment).filter(
            Payment.paddle_transaction_id == transaction_id
        ).first()
        
        # Extract amount from totals (always extract to ensure accuracy)
        totals = data.get("totals", {})
        total_amount = totals.get("total", 0) if isinstance(totals, dict) else 0
        amount_cents = int(float(total_amount) * 100) if total_amount else 0
        
        # Extract currency_code - handle both string and CurrencyCode enum
        currency_code = data.get("currency_code", "USD")
        if hasattr(currency_code, 'value'):
            # It's an enum, get the string value
            currency_code = currency_code.value
        elif not isinstance(currency_code, str):
            # Fallback to string conversion
            currency_code = str(currency_code) if currency_code else 'USD'
        
        if not payment:
            # Create new payment record
            payment = Payment(
                user_id=user_id,
                amount=amount_cents,
                currency=currency_code,
                status=PaymentStatus.COMPLETED,
                paddle_transaction_id=transaction_id,
                paddle_invoice_id=data.get("invoice_id"),
                payment_method=data.get("payment_method_type")
            )
            db.add(payment)
            logger.info(f"Created payment record: payment_id={payment.id}, amount={amount_cents} cents ({currency_code}), transaction_id={transaction_id}")
        else:
            # Update existing payment record
            # Always update amount to ensure accuracy (amounts can change due to discounts, taxes, etc.)
            old_amount = payment.amount
            payment.amount = amount_cents
            payment.currency = currency_code  # Update currency as well
            payment.status = PaymentStatus.COMPLETED
            payment.paddle_invoice_id = data.get("invoice_id") or payment.paddle_invoice_id
            payment.payment_method = data.get("payment_method_type") or payment.payment_method
            logger.info(f"Updated payment record: payment_id={payment.id}, amount={old_amount} -> {amount_cents} cents ({currency_code}), transaction_id={transaction_id}")
        
        # Create or update subscription
        subscription = SubscriptionService.get_active_subscription(user_id, db)
        now = datetime.utcnow()
        
        if not subscription:
            # Create new subscription
            subscription = SubscriptionService.create_subscription(
                user_id=user_id,
                plan=plan,
                billing_period=billing_period,
                paddle_subscription_id=data.get("subscription_id"),
                price_id=price.id if price else None,
                db=db
            )
            # Set billing fields for new subscription
            subscription.last_billing_date = now
            subscription.last_billing_status = "completed"
            # next_billing_date is already set by create_subscription (current_period_end)
            subscription.next_billing_date = subscription.current_period_end
        else:
            # Update existing subscription with new plan
            # This handles upgrades/downgrades and renewals
            old_period_start = subscription.current_period_start
            
            subscription.plan = SubscriptionPlan(plan)
            subscription.billing_period = BillingPeriod(billing_period)
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.paddle_subscription_id = data.get("subscription_id") or subscription.paddle_subscription_id
            if price:
                subscription.price_id = price.id
            
            # Check if this is a renewal (new billing period started)
            # Get period dates from transaction or calculate
            from datetime import timedelta
            new_period_start = None
            if data.get("billing_period") and hasattr(data.get("billing_period"), 'starts_at'):
                new_period_start = data.get("billing_period").starts_at
            elif subscription.current_period_end and subscription.current_period_end < now:
                # Period ended, this is a renewal
                new_period_start = subscription.current_period_end + timedelta(seconds=1)
            
            # Update billing fields
            subscription.last_billing_date = now
            subscription.last_billing_status = "completed"
            
            # Update next billing date based on billing period
            if subscription.current_period_end:
                # If we have a period end, use it as next billing date
                subscription.next_billing_date = subscription.current_period_end
            else:
                # Calculate next billing date from now
                if subscription.billing_period == BillingPeriod.MONTHLY:
                    subscription.next_billing_date = now + timedelta(days=30)
                else:  # YEARLY
                    subscription.next_billing_date = now + timedelta(days=365)
            
            # If billing period renewed, reset keyword search limits
            if new_period_start and old_period_start and new_period_start > old_period_start:
                subscription.current_period_start = new_period_start
                # Reset keyword search limits for new billing period
                from services.subscription_management_service import SubscriptionManagementService
                SubscriptionManagementService.reset_keyword_search_limits_on_renewal(subscription, db)
            
            db.commit()
            db.refresh(subscription)
        
        # Link payment to subscription
        payment.subscription_id = subscription.id
        db.commit()
        
        # Create audit log for payment completion (webhook)
        try:
            audit_log = UserAuditLog(
                user_id=user_id,
                action="payment_completed",
                ip_address=None,  # Webhook doesn't have IP
                user_agent="Paddle Webhook",
                details=f"Payment completed via webhook: transaction_id={transaction_id}, payment_id={payment.id}, amount={payment.amount/100:.2f} {payment.currency}, plan={plan}, billing_period={billing_period}"
            )
            db.add(audit_log)
            db.commit()
        except Exception as e:
            # Don't fail payment processing if audit log fails
            logger.warning(f"Failed to create audit log for payment completion: {str(e)}")
        
        # Send payment receipt email (async, don't block on failure)
        try:
            # Send email in background (don't await to avoid blocking)
            # The email service will handle errors internally
            EmailService.send_payment_receipt_email(
                payment_id=payment.id,
                user_email=user.email,
                user_name=user.full_name,
                amount_cents=payment.amount,
                currency=payment.currency,
                plan=plan,
                billing_period=billing_period,
                transaction_id=transaction_id,
                db=db
            )
        except Exception as e:
            # Don't fail payment processing if email fails
            logger.warning(f"Failed to send payment receipt email: {str(e)}", exc_info=True)
        
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
            
            # Update subscription billing status if subscription exists
            if payment.subscription_id:
                subscription = db.query(Subscription).filter(
                    Subscription.id == payment.subscription_id
                ).first()
                if subscription:
                    subscription.last_billing_status = "failed"
                    subscription.last_billing_date = datetime.utcnow()
                    # Mark subscription as past_due if it's active
                    if subscription.status == SubscriptionStatus.ACTIVE:
                        subscription.status = SubscriptionStatus.PAST_DUE
                    
                    # Create audit log for payment failure (webhook)
                    try:
                        audit_log = UserAuditLog(
                            user_id=payment.user_id,
                            action="payment_failed",
                            ip_address=None,  # Webhook doesn't have IP
                            user_agent="Paddle Webhook",
                            details=f"Payment failed via webhook: transaction_id={transaction_id}, payment_id={payment.id}, subscription_id={subscription.id}"
                        )
                        db.add(audit_log)
                    except Exception as e:
                        # Don't fail payment processing if audit log fails
                        logger.warning(f"Failed to create audit log for payment failure: {str(e)}")
            
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
        
        # Map price_id to plan
        plan = "professional"  # Default
        billing_period = "monthly"  # Default
        
        # Get price from database
        price = None
        if items and len(items) > 0:
            price_id = items[0].get("price_id")
            if price_id:
                price = PriceService.get_price_by_paddle_id(price_id, db)
                if price:
                    plan = price.plan
                    billing_period = price.billing_period.value if price.billing_period else "monthly"
        
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
        """
        Handle subscription.updated webhook event.
        
        This is triggered when:
        - Subscription items are updated (upgrade/downgrade)
        - Billing period changes
        - Subscription status changes
        - Proration is applied
        """
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
            
            # Update billing period dates if available
            old_period_start = subscription.current_period_start
            if data.get("current_billing_period"):
                period = data.get("current_billing_period")
                if isinstance(period, dict):
                    if period.get("starts_at"):
                        new_period_start = datetime.fromisoformat(period["starts_at"].replace("Z", "+00:00"))
                        # Check if billing period renewed
                        if old_period_start and new_period_start > old_period_start:
                            # Billing period renewed - reset keyword search limits
                            from services.subscription_management_service import SubscriptionManagementService
                            SubscriptionManagementService.reset_keyword_search_limits_on_renewal(subscription, db)
                        subscription.current_period_start = new_period_start
                    if period.get("ends_at"):
                        subscription.current_period_end = datetime.fromisoformat(period["ends_at"].replace("Z", "+00:00"))
                        subscription.next_billing_date = subscription.current_period_end
            
            # Update plan and price if items changed
            if data.get("items"):
                items = data.get("items", [])
                if items and len(items) > 0:
                    # Get first item (we only support one item per subscription)
                    item = items[0]
                    if item.get("price_id"):
                        # Find price in database
                        price = PriceService.get_price_by_paddle_id(item["price_id"], db)
                        if price:
                            subscription.plan = SubscriptionPlan(price.plan)
                            subscription.billing_period = price.billing_period
                            subscription.price_id = price.id
            
            db.commit()
            db.refresh(subscription)
        
        return {
            "status": "success",
            "message": "Subscription updated"
        }
    
    @staticmethod
    async def update_subscription_plan(
        subscription: Subscription,
        new_plan: str,
        new_billing_period: str,
        proration_billing_mode: str = "prorated_immediately",
        db: Session = None
    ) -> Dict[str, Any]:
        """
        Update subscription plan (upgrade/downgrade) with proration.
        
        Uses Paddle's subscription update API to change the subscription items
        with automatic proration calculation (similar to Stripe).
        
        Args:
            subscription: Current subscription
            new_plan: New plan name (starter, professional, power)
            new_billing_period: New billing period (monthly, yearly)
            proration_billing_mode: How to handle proration
                - "prorated_immediately": Calculate and bill prorated amount now (Stripe-like)
                - "prorated_next_billing_period": Calculate now, bill on next renewal
                - "full_immediately": Charge full amount now (no proration)
                - "full_next_billing_period": Charge full amount on next renewal
                - "do_not_bill": Change without billing
            db: Database session
            
        Returns:
            Dict with update results
            
        Raises:
            HTTPException: If subscription not found or update fails
            ValueError: If Paddle is disabled
        """
        # Check if Paddle is enabled
        if not PaymentService.is_paddle_enabled():
            raise ValueError(
                "Paddle payment gateway is disabled. "
                "Set PADDLE_ENABLED=true and PADDLE_API_KEY to enable it."
            )
        
        if not subscription.paddle_subscription_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Subscription does not have a Paddle subscription ID. Cannot update via Paddle."
            )
        
        # Get new price
        new_price = PriceService.get_price_by_plan_and_period(new_plan, new_billing_period, db)
        if not new_price:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Price not found for plan: {new_plan}, billing_period: {new_billing_period}"
            )
        
        try:
            paddle = PaymentService.get_paddle_client()
            
            # Get current subscription from Paddle to see current items
            paddle_sub = paddle.subscriptions.get(subscription.paddle_subscription_id)
            
            # Build update items - replace current items with new price
            # Paddle requires us to provide the full list of items
            update_items = [
                {
                    "price_id": new_price.paddle_price_id,
                    "quantity": 1
                }
            ]
            
            # Update subscription using Paddle SDK
            # Reference: https://developer.paddle.com/api-reference/subscriptions/update-subscription
            updated_subscription = paddle.subscriptions.update(
                subscription.paddle_subscription_id,
                UpdateSubscription(
                    items=update_items,
                    proration_billing_mode=proration_billing_mode
                )
            )
            
            logger.info(
                f"Updated subscription {subscription.id} via Paddle: "
                f"{subscription.plan.value} -> {new_plan}, "
                f"proration_mode: {proration_billing_mode}"
            )
            
            # Update local subscription record
            # Note: We'll get the full details from webhook, but update basic info now
            subscription.plan = SubscriptionPlan(new_plan)
            try:
                subscription.billing_period = BillingPeriod(new_billing_period.lower())
            except ValueError:
                subscription.billing_period = BillingPeriod.MONTHLY
            
            subscription.price_id = new_price.id
            
            # Update billing period dates from Paddle response if available
            if hasattr(updated_subscription, 'current_billing_period') and updated_subscription.current_billing_period:
                period = updated_subscription.current_billing_period
                if hasattr(period, 'starts_at') and period.starts_at:
                    subscription.current_period_start = period.starts_at
                if hasattr(period, 'ends_at') and period.ends_at:
                    subscription.current_period_end = period.ends_at
                    subscription.next_billing_date = period.ends_at
            
            db.commit()
            db.refresh(subscription)
            
            return {
                "status": "success",
                "message": "Subscription updated successfully",
                "subscription_id": subscription.id,
                "old_plan": subscription.plan.value,
                "new_plan": new_plan,
                "proration_mode": proration_billing_mode
            }
            
        except ApiError as e:
            logger.error(f"Paddle API error updating subscription: {e.error_code} - {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to update subscription in Paddle: {e.error_code} - {str(e)}"
            )
        except Exception as e:
            logger.error(f"Error updating subscription: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update subscription: {str(e)}"
            )
    
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
    
    @staticmethod
    async def verify_and_complete_transaction(
        transaction_id: str,
        user_id: str,
        db: Session
    ) -> Dict[str, Any]:
        """
        Verify and complete a Paddle transaction.
        
        This method:
        1. Checks if payment already exists in our database
        2. If not, fetches transaction from Paddle
        3. Verifies transaction belongs to the user
        4. Processes the transaction (creates payment, updates subscription)
        
        Args:
            transaction_id: Paddle transaction ID
            user_id: User ID to verify ownership
            db: Database session
            
        Returns:
            Dict with status, message, and related IDs
            
        Raises:
            HTTPException: If transaction not found, invalid, or already processed by different user
        """
        # Check if payment already exists
        existing_payment = db.query(Payment).filter(
            Payment.paddle_transaction_id == transaction_id
        ).first()
        
        if existing_payment:
            # Payment already processed
            if existing_payment.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This transaction belongs to a different user"
                )
            
            # Get subscription info
            subscription = None
            if existing_payment.subscription_id:
                subscription = db.query(Subscription).filter(
                    Subscription.id == existing_payment.subscription_id
                ).first()
            
            return {
                "status": "already_processed",
                "message": "Transaction already processed successfully",
                "payment_id": existing_payment.id,
                "subscription_id": subscription.id if subscription else None,
                "subscription_status": subscription.status.value if subscription else None,
                "plan": subscription.plan.value if subscription else None
            }
        
        # Fetch transaction from Paddle
        try:
            paddle = PaymentService.get_paddle_client()
            transaction = paddle.transactions.get(transaction_id)
        except ApiError as e:
            if "not_found" in str(e).lower() or e.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Transaction not found in Paddle"
                )
            logger.error(f"Error fetching transaction from Paddle: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to verify transaction with Paddle"
            )
        except Exception as e:
            logger.error(f"Unexpected error fetching transaction: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while verifying the transaction"
            )
        
        # Verify transaction status
        # Transactions can be 'billed' (marked as billed to lock quantity) or 'completed' (payment successful)
        # We need 'completed' status to process the payment and create subscription
        transaction_status = getattr(transaction, 'status', None)
        if transaction_status not in ['completed', 'billed']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Transaction is not ready for processing. Current status: {transaction_status}. Please wait for payment to complete."
            )
        
        # If transaction is 'billed' but not 'completed', it means payment hasn't been processed yet
        # In this case, we should wait for the webhook or check again later
        if transaction_status == 'billed':
            # Check if there's already a payment record (might have been processed via webhook)
            existing_payment = db.query(Payment).filter(
                Payment.paddle_transaction_id == transaction_id
            ).first()
            
            if existing_payment and existing_payment.status == PaymentStatus.COMPLETED:
                # Payment already processed, return existing subscription info
                subscription = None
                if existing_payment.subscription_id:
                    subscription = db.query(Subscription).filter(
                        Subscription.id == existing_payment.subscription_id
                    ).first()
                
                return {
                    "status": "already_processed",
                    "message": "Transaction is billed and payment is being processed. Subscription will be activated once payment completes.",
                    "payment_id": existing_payment.id,
                    "subscription_id": subscription.id if subscription else None,
                    "subscription_status": subscription.status.value if subscription else None,
                    "plan": subscription.plan.value if subscription else None
                }
            
            # Transaction is billed but payment not completed yet
            raise HTTPException(
                status_code=status.HTTP_202_ACCEPTED,
                detail="Transaction is billed but payment is still processing. Please wait for payment to complete. You will receive a confirmation email once the subscription is activated."
            )
        
        # Get user
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Verify transaction belongs to user (check customer_id)
        transaction_customer_id = getattr(transaction, 'customer_id', None)
        if transaction_customer_id and user.paddle_customer_id:
            if transaction_customer_id != user.paddle_customer_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Transaction does not belong to this user"
                )
        elif transaction_customer_id:
            # Update user's Paddle customer ID
            user.paddle_customer_id = transaction_customer_id
            db.commit()
        
        # Extract transaction data for processing
        # Handle totals - Paddle SDK returns totals as an object with total, subtotal, tax, etc.
        totals_dict = {}
        if hasattr(transaction, 'totals') and transaction.totals:
            if hasattr(transaction.totals, 'total'):
                totals_dict['total'] = str(transaction.totals.total) if transaction.totals.total else '0'
            elif isinstance(transaction.totals, dict):
                totals_dict = transaction.totals
            else:
                # Try to convert to dict
                totals_dict = {'total': '0'}
        else:
            totals_dict = {'total': '0'}
        
        # Extract currency_code - handle both string and CurrencyCode enum
        currency_code = getattr(transaction, 'currency_code', 'USD')
        if hasattr(currency_code, 'value'):
            # It's an enum, get the string value
            currency_code = currency_code.value
        elif not isinstance(currency_code, str):
            # Fallback to string conversion
            currency_code = str(currency_code) if currency_code else 'USD'
        
        transaction_data = {
            "id": transaction_id,
            "customer_id": transaction_customer_id,
            "status": "completed",
            "currency_code": currency_code,
            "totals": totals_dict,
            "payment_method_type": getattr(transaction, 'payment_method_type', None),
            "invoice_id": getattr(transaction, 'invoice_id', None),
            "subscription_id": getattr(transaction, 'subscription_id', None)
        }
        
        # Get plan from transaction items
        plan = "professional"  # Default
        billing_period = "monthly"  # Default
        
        if hasattr(transaction, 'items') and transaction.items:
            # Get price_id from first item
            items_list = transaction.items if isinstance(transaction.items, list) else [transaction.items]
            if items_list:
                first_item = items_list[0]
                # Try different ways to get price_id
                price_id = None
                if hasattr(first_item, 'price_id'):
                    price_id = first_item.price_id
                elif hasattr(first_item, 'price') and hasattr(first_item.price, 'id'):
                    price_id = first_item.price.id
                elif isinstance(first_item, dict):
                    price_id = first_item.get('price_id') or (first_item.get('price', {}) or {}).get('id')
                
                if price_id:
                    price = PriceService.get_price_by_paddle_id(price_id, db)
                    if price:
                        plan = price.plan
                        billing_period = price.billing_period.value if price.billing_period else "monthly"
        
        # Add custom_data with user_id and plan for processing
        transaction_data['custom_data'] = {
            'user_id': user_id,
            'plan': plan,
            'billing_period': billing_period
        }
        
        # Process transaction (similar to webhook handler)
        result = await PaymentService._handle_transaction_completed(
            transaction_data,
            db
        )
        
        # Get updated subscription
        subscription = SubscriptionService.get_active_subscription(user_id, db)
        
        # Send payment receipt email (async, don't block on failure)
        try:
            payment_id = result.get("payment_id")
            if payment_id:
                # Send email in background (don't await to avoid blocking)
                # The email service will handle errors internally
                EmailService.send_payment_receipt_email(
                    payment_id=payment_id,
                    user_email=user.email,
                    user_name=user.full_name,
                    amount_cents=int(float(totals_dict.get('total', 0)) * 100) if totals_dict.get('total') else 0,
                    currency=currency_code,
                    plan=plan,
                    billing_period=billing_period,
                    transaction_id=transaction_id,
                    db=db
                )
        except Exception as e:
            # Don't fail payment processing if email fails
            logger.warning(f"Failed to send payment receipt email: {str(e)}", exc_info=True)
        
        return {
            "status": "success",
            "message": "Transaction verified and processed successfully",
            "payment_id": result.get("payment_id"),
            "subscription_id": subscription.id if subscription else None,
            "subscription_status": subscription.status.value if subscription else None,
            "plan": subscription.plan.value if subscription else plan
        }
    
    @staticmethod
    async def mark_transaction_as_billed(
        transaction_id: str,
        user_id: str,
        db: Session,
        wait_for_ready: bool = True,
        max_wait_seconds: int = 10
    ) -> Dict[str, Any]:
        """
        Mark a transaction as billed to lock quantity.
        
        According to Paddle docs, marking a transaction as billed once it's ready
        prevents customers from changing quantity. The quantity stepper is still shown,
        but customers get an error if they try to change it.
        
        Reference: https://developer.paddle.com/build/checkout/pass-update-checkout-items
        
        This method polls the transaction status until it becomes 'ready' (if wait_for_ready=True),
        then marks it as billed. Transactions start in 'draft' and become 'ready' when checkout loads.
        
        Args:
            transaction_id: Paddle transaction ID
            user_id: User ID to verify ownership
            db: Database session
            wait_for_ready: If True, poll until transaction is ready (default: True)
            max_wait_seconds: Maximum seconds to wait for transaction to become ready (default: 10)
            
        Returns:
            Dict with status and message
            
        Raises:
            HTTPException: If transaction not found, not ready, or doesn't belong to user
        """
        # Verify transaction belongs to user
        payment = db.query(Payment).filter(
            Payment.paddle_transaction_id == transaction_id
        ).first()
        
        if payment and payment.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This transaction belongs to a different user"
            )
        
        # Fetch transaction from Paddle and poll until ready if needed
        paddle = PaymentService.get_paddle_client()
        transaction = None
        transaction_status = None
        
        if wait_for_ready:
            # Poll for transaction to become ready
            # Transactions start in "draft" and need customer_id/address_id to become "ready"
            # For checkout transactions, Paddle sets these automatically when checkout loads
            # and customer enters their information
            # IMPORTANT: We poll aggressively (every 0.5s) to minimize race condition window
            # where user could change quantity before transaction is marked as billed
            import asyncio
            import time
            
            start_time = time.time()
            poll_interval = 0.5  # Poll every 0.5 seconds (aggressive to prevent race condition)
            max_attempts = int(max_wait_seconds / poll_interval)
            attempt = 0
            
            logger.info(f"Polling transaction {transaction_id} until it becomes 'ready' (max {max_wait_seconds}s, {max_attempts} attempts, interval: {poll_interval}s)")
            
            while attempt < max_attempts:
                try:
                    transaction = paddle.transactions.get(transaction_id)
                    transaction_status = getattr(transaction, 'status', None)
                    
                    elapsed = time.time() - start_time
                    logger.debug(f"Transaction {transaction_id} status check {attempt + 1}/{max_attempts}: {transaction_status} (elapsed: {elapsed:.1f}s)")
                    
                    if transaction_status == 'ready':
                        logger.info(f"Transaction {transaction_id} is now ready after {elapsed:.1f}s")
                        break
                    elif transaction_status in ['completed', 'billed']:
                        # Already completed/billed, nothing to do
                        logger.info(f"Transaction {transaction_id} is already {transaction_status}. Quantity is locked.")
                        return {
                            "status": "success",
                            "message": f"Transaction is already {transaction_status}. Quantity is locked."
                        }
                    elif transaction_status == 'draft':
                        # Still in draft, wait and retry
                        if elapsed >= max_wait_seconds:
                            logger.warning(f"Transaction {transaction_id} still in 'draft' after {max_wait_seconds}s. Customer may not have entered details yet.")
                            break
                        
                        # Wait before next poll
                        await asyncio.sleep(poll_interval)
                        attempt += 1
                    else:
                        # Unknown status, log and break
                        logger.warning(f"Transaction {transaction_id} in unexpected status: {transaction_status}")
                        break
                        
                except ApiError as e:
                    if "not_found" in str(e).lower() or e.status_code == 404:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail="Transaction not found in Paddle"
                        )
                    logger.warning(f"Error polling transaction status (attempt {attempt + 1}): {str(e)}")
                    # Wait before retrying
                    await asyncio.sleep(poll_interval)
                    attempt += 1
                except Exception as e:
                    logger.warning(f"Unexpected error polling transaction: {str(e)}", exc_info=True)
                    break
        else:
            # Don't wait, just fetch once
            try:
                transaction = paddle.transactions.get(transaction_id)
                transaction_status = getattr(transaction, 'status', None)
            except ApiError as e:
                if "not_found" in str(e).lower() or e.status_code == 404:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Transaction not found in Paddle"
                    )
                logger.error(f"Error fetching transaction from Paddle: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to fetch transaction from Paddle"
                )
            except Exception as e:
                logger.error(f"Unexpected error fetching transaction: {str(e)}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="An error occurred while fetching the transaction"
                )
        
        # Verify transaction status is "ready" (required by Paddle to mark as billed)
        # According to Paddle docs, transactions must be in "ready" status before marking as billed
        # Transaction lifecycle: draft → ready → completed/billed
        if transaction_status in ['completed', 'billed']:
            # Already completed/billed, nothing to do
            return {
                "status": "success",
                "message": "Transaction is already completed/billed. Quantity is locked."
            }
        
        if transaction_status != 'ready':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Transaction must be in 'ready' status to mark as billed. "
                    f"Current status: '{transaction_status}'. "
                    f"Transaction may still be initializing. Please try again in a moment."
                )
            )
        
        # Verify transaction belongs to user (check customer_id)
        transaction_customer_id = getattr(transaction, 'customer_id', None)
        if transaction_customer_id:
            user = db.query(User).filter(User.id == user_id).first()
            if user and user.paddle_customer_id:
                if transaction_customer_id != user.paddle_customer_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Transaction does not belong to this user"
                    )
        
        # Mark transaction as billed
        # Try using SDK first, fallback to direct API call if needed
        try:
            # Use Paddle SDK to update transaction status to billed
            # This locks the transaction and prevents quantity changes
            updated_transaction = paddle.transactions.update(
                transaction_id,
                UpdateTransaction(
                    status="billed"
                )
            )
            
            logger.info(f"Transaction {transaction_id} marked as billed to lock quantity")
            
            return {
                "status": "success",
                "message": "Transaction marked as billed. Quantity is now locked."
            }
        except ApiError as e:
            # If SDK fails, try direct API call as fallback
            # This allows us to bypass SDK limitations if needed
            logger.warning(f"SDK failed to mark transaction as billed, trying direct API call: {str(e)}")
            try:
                import httpx
                
                # Get API base URL based on environment
                api_base_url = "https://sandbox-api.paddle.com" if settings.PADDLE_ENVIRONMENT.lower() == "sandbox" else "https://api.paddle.com"
                
                # Make direct PATCH request to Paddle API
                # Reference: https://developer.paddle.com/api-reference/transactions/update-transaction
                async with httpx.AsyncClient() as client:
                    response = await client.patch(
                        f"{api_base_url}/transactions/{transaction_id}",
                        headers={
                            "Authorization": f"Bearer {settings.PADDLE_API_KEY}",
                            "Content-Type": "application/json",
                        },
                        json={"status": "billed"},
                        timeout=15.0
                    )
                    
                    if response.status_code == 200:
                        logger.info(f"Transaction {transaction_id} marked as billed via direct API call")
                        return {
                            "status": "success",
                            "message": "Transaction marked as billed. Quantity is now locked."
                        }
                    else:
                        error_text = response.text
                        logger.error(f"Direct API call failed: {response.status_code} - {error_text}")
                        
                        # If still not ready, return a more helpful error
                        if response.status_code == 400 and ("ready" in error_text.lower() or "draft" in error_text.lower()):
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=(
                                    f"Transaction must be in 'ready' status to mark as billed. "
                                    f"Transaction may still be initializing. Please wait for checkout to fully load "
                                    f"and customer to enter their details."
                                )
                            )
                        
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Failed to mark transaction as billed: {error_text}"
                        )
            except ImportError:
                logger.error("httpx not available for direct API call fallback")
                # Re-raise the original SDK error
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to mark transaction as billed: {str(e)}"
                )
            except HTTPException:
                # Re-raise HTTP exceptions (like the 400 we raise above)
                raise
            except Exception as direct_error:
                logger.error(f"Direct API call also failed: {str(direct_error)}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to mark transaction as billed: {str(e)}"
                )
        except Exception as e:
            logger.error(f"Unexpected error marking transaction as billed: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while marking the transaction as billed"
            )
