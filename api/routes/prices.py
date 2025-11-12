"""
Price Routes

Handles price management API endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from core.database import get_db
from api.dependencies import get_current_user
from models.user import User
from models.price import Price, BillingPeriod
from services.price_service import PriceService

router = APIRouter()


# Request/Response Models
class PriceResponse(BaseModel):
    """Price response model."""
    id: str
    plan: str
    billing_period: str
    paddle_price_id: str
    paddle_product_id: Optional[str]
    amount: int
    currency: str
    is_active: bool
    created_at: str
    updated_at: str


class PriceCreate(BaseModel):
    """Price creation request model."""
    plan: str
    billing_period: str
    paddle_price_id: str
    amount: int
    currency: str = "USD"
    paddle_product_id: Optional[str] = None


@router.get("/", response_model=List[PriceResponse])
async def list_prices(
    plan: Optional[str] = None,
    billing_period: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: Session = Depends(get_db)
):
    """
    List all prices with optional filters.
    
    **Query Parameters**:
    - plan: Filter by plan (starter, professional, power)
    - billing_period: Filter by billing period (monthly, yearly)
    - is_active: Filter by active status (default: true)
    
    **Response 200**:
    - List of prices
    """
    query = db.query(Price)
    
    if is_active is not None:
        query = query.filter(Price.is_active == is_active)
    
    if plan:
        query = query.filter(Price.plan == plan)
    
    if billing_period:
        try:
            billing_period_enum = BillingPeriod(billing_period.lower())
            query = query.filter(Price.billing_period == billing_period_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid billing_period: {billing_period}"
            )
    
    prices = query.all()
    return [price.to_dict() for price in prices]


@router.get("/{price_id}", response_model=PriceResponse)
async def get_price(
    price_id: str,
    db: Session = Depends(get_db)
):
    """
    Get price by ID.
    
    **Path Parameters**:
    - price_id: Price UUID
    
    **Response 200**:
    - Price details
    
    **Response 404**: Price not found
    """
    price = db.query(Price).filter(Price.id == price_id).first()
    
    if not price:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Price not found"
        )
    
    return price.to_dict()


@router.post("/", response_model=PriceResponse, status_code=status.HTTP_201_CREATED)
async def create_price(
    price_data: PriceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new price.
    
    **Authentication Required**: Yes (JWT token)
    
    **Request Body**:
    - plan: Subscription plan
    - billing_period: Billing period (monthly, yearly)
    - paddle_price_id: Paddle price ID
    - amount: Amount in cents
    - currency: Currency code (default: USD)
    - paddle_product_id: Paddle product ID (optional)
    
    **Response 201**:
    - Created price
    
    **Response 400**: Invalid data
    **Response 401**: Not authenticated
    """
    try:
        price = PriceService.create_or_update_price(
            plan=price_data.plan,
            billing_period=price_data.billing_period,
            paddle_price_id=price_data.paddle_price_id,
            amount=price_data.amount,
            currency=price_data.currency,
            paddle_product_id=price_data.paddle_product_id,
            db=db
        )
        
        return price.to_dict()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/plan/{plan}", response_model=List[PriceResponse])
async def get_prices_by_plan(
    plan: str,
    db: Session = Depends(get_db)
):
    """
    Get all prices for a specific plan.
    
    **Path Parameters**:
    - plan: Subscription plan (starter, professional, power)
    
    **Response 200**:
    - List of prices for the plan
    """
    prices = PriceService.get_prices_by_plan(plan, db)
    return [price.to_dict() for price in prices]

