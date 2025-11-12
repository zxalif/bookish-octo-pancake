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
from models.user import User
from models.payment import Payment
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
    """Checkout response model."""
    checkout_url: str
    transaction_id: Optional[str] = None
    customer_id: Optional[str] = None


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
    
    # Removed fields:
    # - user_id: Not needed (user already authenticated via JWT)
    # - paddle_transaction_id: Internal Paddle ID, not used by frontend
    # - paddle_invoice_id: Internal Paddle ID, not used by frontend


@router.post("/paddle/create-checkout", response_model=CheckoutResponse)
async def create_paddle_checkout(
    checkout_data: CheckoutCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a Paddle checkout session for subscription.
    
    This endpoint creates a checkout session with Paddle and returns a checkout URL
    that the user can redirect to for payment.
    
    **Authentication Required**: Yes (JWT token)
    
    **Request Body**:
    - plan: Subscription plan (starter, professional, power)
    
    **Response 200**:
    - checkout_url: URL to redirect user to for payment
    - transaction_id: Paddle transaction ID (if available)
    - customer_id: Paddle customer ID (if available)
    
    **Response 400**: Invalid plan
    **Response 401**: Not authenticated
    **Response 502**: Paddle API error
    """
    try:
        result = await PaymentService.create_checkout_session(
            user_id=current_user.id,
            plan=checkout_data.plan,
            billing_period=checkout_data.billing_period,
            db=db
        )
        
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
    **Security**: Webhook signature verification
    
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
    
    # Verify webhook signature
    if paddle_signature:
        if not PaymentService.verify_webhook_signature(body, paddle_signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )
    
    # Parse webhook payload
    try:
        event_data = await request.json()
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
        # In production, you should log this properly
        return {
            "status": "error",
            "message": f"Error processing webhook: {str(e)}"
        }


@router.get("/history")
async def get_payment_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user's payment history.
    
    Returns all payments for the authenticated user.
    
    **Authentication Required**: Yes (JWT token)
    
    **Response 200**:
    - List of payment records
    
    **Response 401**: Not authenticated
    """
    payments = db.query(Payment).filter(
        Payment.user_id == current_user.id
    ).order_by(Payment.created_at.desc()).all()
    
    # Use Pydantic model to ensure only expected fields are returned
    payment_responses = [
        PaymentResponse(
            id=payment.id,
            subscription_id=payment.subscription_id,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status.value,
            payment_method=payment.payment_method,
            created_at=payment.created_at.isoformat() if payment.created_at else "",
            updated_at=payment.updated_at.isoformat() if payment.updated_at else "",
        )
        for payment in payments
    ]
    
    return {
        "total": len(payments),
        "payments": payment_responses
    }


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get payment details by ID.
    
    **Authentication Required**: Yes (JWT token)
    
    **Path Parameters**:
    - payment_id: Payment UUID
    
    **Response 200**:
    - Payment details
    
    **Response 404**: Payment not found or doesn't belong to user
    **Response 401**: Not authenticated
    """
    payment = db.query(Payment).filter(
        Payment.id == payment_id,
        Payment.user_id == current_user.id
    ).first()
    
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found"
        )
    
    # Use Pydantic model to ensure only expected fields are returned
    return PaymentResponse(
        id=payment.id,
        subscription_id=payment.subscription_id,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status.value,
        payment_method=payment.payment_method,
        created_at=payment.created_at.isoformat() if payment.created_at else "",
        updated_at=payment.updated_at.isoformat() if payment.updated_at else "",
    )
