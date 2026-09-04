from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.models.user import User
from app.services.billing_service import billing_service


router = APIRouter()


class OrderCreateRequest(BaseModel):
    plan: Literal["ENTERPRISE"]


class PaymentConfirmRequest(BaseModel):
    paymentKey: str = Field(min_length=10, max_length=300)
    orderId: str = Field(min_length=6, max_length=64)
    amount: int = Field(gt=0)


class PaymentFailureRequest(BaseModel):
    code: str | None = Field(default=None, max_length=100)
    message: str | None = Field(default=None, max_length=500)
    canceled: bool = False


@router.post("/orders")
def create_order(payload: OrderCreateRequest, user: User = Depends(require_current_user)) -> dict:
    return billing_service.create_order(user, payload.plan)


@router.post("/payments/confirm")
def confirm_payment(payload: PaymentConfirmRequest, user: User = Depends(require_current_user)) -> dict:
    return billing_service.confirm_payment(user, payload.paymentKey, payload.orderId, payload.amount)


@router.post("/orders/{order_id}/failure")
def record_failure(order_id: str, payload: PaymentFailureRequest, user: User = Depends(require_current_user)) -> dict:
    status = "CANCELED" if payload.canceled else "FAILED"
    return billing_service.mark_order(user, order_id, status, payload.code, payload.message)
