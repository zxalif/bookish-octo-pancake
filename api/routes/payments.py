"""
Payment Routes

Handles Paddle payment integration and webhooks.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from core.database import get_db
from core.logger import get_logger
from api.dependencies import get_current_user
from api.middleware.rate_limit import limiter
from slowapi.util import get_remote_address
from models.user import User
from models.payment import Payment
from models.user_audit_log import UserAuditLog
from services.payment_service import PaymentService

logger = get_logger(__name__)

router = APIRouter()


# Request/Response Models
class CheckoutCreate(BaseModel):
    """Checkout creation request model."""
    plan: str  # starter, professional, power
    billing_period: str = "monthly"  # monthly or yearly
    
    class Config:
        json_schema_extra = {
            "example": {
                "plan": "professional",
                "billing_period": "monthly"
            }
        }


class CheckoutResponse(BaseModel):
    """Checkout response model.
    
    Note: customer_id and price_id are included for frontend functionality
    but are not sensitive (customer_id is user's own Paddle customer ID,
    price_id is public pricing information).
    """
    checkout_url: str
    transaction_id: Optional[str] = None  # Paddle transaction ID (needed for frontend checkout)
    customer_id: Optional[str] = None  # User's own Paddle customer ID (not sensitive)
    price_id: Optional[str] = None  # Price ID for locking quantity in checkout (public pricing info)


class PaymentResponse(BaseModel):
    """Payment response model."""
    id: str
    subscription_id: str | None
    amount: int  # Amount in cents
    currency: str
    status: str
    payment_method: str | None
    created_at: str
    updated_at: str
    formatted_amount: str | None = None  # Human-readable amount (e.g., "$29.99")
    
    # Removed fields:
    # - user_id: Not needed (user already authenticated via JWT)
    # - paddle_transaction_id: Internal Paddle ID, not used by frontend
    # - paddle_invoice_id: Internal Paddle ID, not used by frontend
    
    @classmethod
    def from_payment(cls, payment: Payment) -> "PaymentResponse":
        """Create PaymentResponse from Payment model with formatted amount."""
        # Format amount: convert cents to dollars and add currency symbol
        if payment.amount:
            currency_symbol = "$" if payment.currency == "USD" else payment.currency
            formatted_amount = f"{currency_symbol}{payment.amount / 100:.2f}"
        else:
            formatted_amount = None
        return cls(
            id=payment.id,
            subscription_id=payment.subscription_id,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status.value,
            payment_method=payment.payment_method,
            created_at=payment.created_at.isoformat() if payment.created_at else "",
            updated_at=payment.updated_at.isoformat() if payment.updated_at else "",
            formatted_amount=formatted_amount
        )


@router.post("/paddle/create-checkout", response_model=CheckoutResponse)
@limiter.limit("10/minute")
async def create_paddle_checkout(
    request: Request,
    checkout_data: CheckoutCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a Paddle checkout session for subscription.
    
    This endpoint creates a checkout session with Paddle and returns a checkout URL
    that the user can redirect to for payment.
    
    **SECURITY**: Rate limited to 10 requests per minute per IP to prevent abuse.
    
    **Authentication Required**: Yes (JWT token)
    
    **Request Body**:
    - plan: Subscription plan (starter, professional, power)
    
    **Response 200**:
    - checkout_url: URL to redirect user to for payment
    - transaction_id: Paddle transaction ID (if available)
    - customer_id: Paddle customer ID (if available)
    
    **Response 400**: Invalid plan
    **Response 401**: Not authenticated
    **Response 429**: Rate limit exceeded
    **Response 502**: Paddle API error
    """
    try:
        result = await PaymentService.create_checkout_session(
            user_id=current_user.id,
            plan=checkout_data.plan,
            billing_period=checkout_data.billing_period,
            db=db
        )
        
        # Create audit log for checkout creation
        ip_address = get_remote_address(request)
        user_agent = request.headers.get("user-agent", "")
        audit_log = UserAuditLog(
            user_id=current_user.id,
            action="checkout_created",
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"Checkout created for plan: {checkout_data.plan}, billing_period: {checkout_data.billing_period}, transaction_id: {result.get('transaction_id', 'N/A')}"
        )
        db.add(audit_log)
        db.commit()
        
        return CheckoutResponse(**result)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": str(e),
                "error_code": "validation_error"
            }
        )
    except HTTPException as e:
        # Re-raise HTTPExceptions (already formatted with proper error structure)
        raise
    except Exception as e:
        logger.error(f"Unexpected error in create_paddle_checkout: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "An unexpected error occurred. Please try again or contact support.",
                "error_code": "internal_error"
            }
        )


@router.post("/paddle/webhook")
async def paddle_webhook(
    request: Request,
    db: Session = Depends(get_db),
    paddle_signature: Optional[str] = Header(None, alias="paddle-signature")
):
    """
    Handle Paddle webhook events.
    
    This endpoint receives webhook events from Paddle for:
    - Transaction completion
    - Payment failures
    - Subscription creation/updates/cancellations
    
    **Authentication**: None (webhook endpoint)
    **Security**: Webhook signature verification using Paddle SDK
    
    **Headers**:
    - paddle-signature: Paddle webhook signature (for verification)
    
    **Request Body**:
    - Paddle webhook event JSON
    
    **Response 200**:
    - Processing result
    
    **Response 401**: Invalid webhook signature
    **Response 400**: Invalid webhook payload
    """
    # Get raw body for signature verification
    body = await request.body()
    
    # Verify webhook signature using Paddle SDK
    if paddle_signature:
        # Create a simple request-like object for the SDK verifier
        # The SDK's Verifier expects an object with headers and body attributes
        class WebhookRequest:
            def __init__(self, headers: dict, body: bytes):
                self.headers = headers
                self.body = body
        
        webhook_request = WebhookRequest(
            headers={"paddle-signature": paddle_signature},
            body=body
        )
        
        if not PaymentService.verify_webhook_signature(webhook_request, paddle_signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )
    
    # Parse webhook payload
    try:
        import json
        event_data = json.loads(body.decode('utf-8'))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid webhook payload: {str(e)}"
        )
    
    # Handle webhook event
    try:
        result = await PaymentService.handle_webhook_event(event_data, db)
        return result
    except Exception as e:
        # Log error but return 200 to Paddle (so they don't retry)
        logger.error(f"Error processing webhook: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Error processing webhook: {str(e)}"
        }


class VerifyTransactionRequest(BaseModel):
    """Transaction verification request model."""
    transaction_id: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "transaction_id": "txn_01k9yynpc38jh8cjz9g4aet2tg"
            }
        }


class VerifyTransactionResponse(BaseModel):
    """Transaction verification response model."""
    status: str
    message: str
    payment_id: Optional[str] = None
    subscription_id: Optional[str] = None
    subscription_status: Optional[str] = None
    plan: Optional[str] = None


@router.post("/verify-transaction", response_model=VerifyTransactionResponse)
@limiter.limit("10/minute")
async def verify_transaction(
    request: Request,
    verify_data: VerifyTransactionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Verify and complete a Paddle transaction.
    
    This endpoint:
    - Verifies the transaction exists in Paddle
    - Checks if payment is already processed
    - If not processed, fetches transaction details from Paddle and processes it
    - Updates user subscription if needed
    
    **Authentication**: Required (JWT token)
    **Rate Limit**: 10 requests per minute
    
    **Request Body**:
    - transaction_id: Paddle transaction ID from success page URL
    
    **Response 200**:
    - status: "success" or "already_processed"
    - message: Human-readable message
    - payment_id: Internal payment ID (if processed)
    - subscription_id: Subscription ID (if created/updated)
    - subscription_status: Current subscription status
    - plan: Subscription plan name
    
    **Response 404**: Transaction not found
    **Response 400**: Invalid transaction or already processed by different user
    """
    try:
        result = await PaymentService.verify_and_complete_transaction(
            transaction_id=verify_data.transaction_id,
            user_id=current_user.id,
            db=db
        )
        
        # Create audit log for transaction verification
        ip_address = get_remote_address(request)
        user_agent = request.headers.get("user-agent", "")
        audit_log = UserAuditLog(
            user_id=current_user.id,
            action="transaction_verified",
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"Transaction verified: {verify_data.transaction_id}, status: {result.get('status', 'N/A')}, payment_id: {result.get('payment_id', 'N/A')}, subscription_id: {result.get('subscription_id', 'N/A')}"
        )
        db.add(audit_log)
        db.commit()
        
        return VerifyTransactionResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying transaction: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "An error occurred while verifying the transaction. Please contact support.",
                "error_code": "internal_error"
            }
        )


class MarkTransactionBilledRequest(BaseModel):
    """Mark transaction as billed request model."""
    transaction_id: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "transaction_id": "txn_01k9yynpc38jh8cjz9g4aet2tg"
            }
        }


@router.post("/mark-transaction-billed")
@limiter.limit("20/minute")
async def mark_transaction_billed(
    request: Request,
    billed_data: MarkTransactionBilledRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Mark a transaction as billed to lock quantity.
    
    This endpoint is called from the frontend when checkout is loaded/ready.
    Marking a transaction as billed prevents customers from changing quantity.
    
    **Authentication**: Required (JWT token)
    **Rate Limit**: 20 requests per minute
    
    **Request Body**:
    - transaction_id: Paddle transaction ID
    
    **Response 200**:
    - status: "success"
    - message: Confirmation message
    
    **Response 404**: Transaction not found
    **Response 400**: Transaction cannot be marked as billed
    """
    try:
        # Poll for transaction to become ready (max 15 seconds)
        # Transactions need customer_id/address_id to become ready, which happens when checkout loads
        result = await PaymentService.mark_transaction_as_billed(
            transaction_id=billed_data.transaction_id,
            user_id=current_user.id,
            db=db,
            wait_for_ready=True,
            max_wait_seconds=15  # Give checkout time to load and customer to enter details
        )
        
        # Create audit log for marking transaction as billed
        ip_address = get_remote_address(request)
        user_agent = request.headers.get("user-agent", "")
        audit_log = UserAuditLog(
            user_id=current_user.id,
            action="transaction_billed",
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"Transaction marked as billed: {billed_data.transaction_id}, status: {result.get('status', 'N/A')}"
        )
        db.add(audit_log)
        db.commit()
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking transaction as billed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "An error occurred while marking the transaction as billed.",
                "error_code": "internal_error"
            }
        )


@router.get("/history")
@limiter.limit("30/minute")
async def get_payment_history(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user's payment history.
    
    Returns all payments for the authenticated user.
    
    **SECURITY**: Rate limited to 30 requests per minute per IP to prevent abuse.
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    - List of payment records (only for authenticated user)
    
    **Response 401**: Not authenticated
    **Response 429**: Rate limit exceeded
    """
    # Security: Only return payments for the authenticated user
    # The filter ensures user-level permission enforcement
    payments = db.query(Payment).filter(
        Payment.user_id == current_user.id
    ).order_by(Payment.created_at.desc()).all()
    
    # Use Pydantic model to ensure only expected fields are returned
    # This prevents leaking sensitive data like paddle_transaction_id, paddle_invoice_id, etc.
    payment_responses = [
        PaymentResponse.from_payment(payment)
        for payment in payments
    ]
    
    return {
        "total": len(payments),
        "payments": payment_responses
    }


@router.get("/{payment_id}", response_model=PaymentResponse)
@limiter.limit("30/minute")
async def get_payment(
    request: Request,
    payment_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get payment details by ID.
    
    **SECURITY**: 
    - Rate limited to 30 requests per minute per IP
    - User-level permission: Only returns payment if it belongs to authenticated user
    
    **Authentication Required**: Yes (JWT token)
    
    **Path Parameters**:
    - payment_id: Payment UUID
    
    **Response 200**:
    - Payment details (only if payment belongs to authenticated user)
    
    **Response 404**: Payment not found or doesn't belong to user
    **Response 401**: Not authenticated
    **Response 429**: Rate limit exceeded
    """
    # Security: Verify payment belongs to authenticated user
    # This enforces user-level permissions - users can only access their own payments
    payment = db.query(Payment).filter(
        Payment.id == payment_id,
        Payment.user_id == current_user.id  # Critical: User-level permission check
    ).first()
    
    if not payment:
        # Don't reveal if payment exists but belongs to another user
        # Return 404 to prevent information disclosure
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found"
        )
    
    # Use Pydantic model to ensure only expected fields are returned
    # This prevents leaking sensitive data like:
    # - paddle_transaction_id (internal Paddle ID)
    # - paddle_invoice_id (internal Paddle ID)
    # - user_id (already known from authentication)
    return PaymentResponse.from_payment(payment)
