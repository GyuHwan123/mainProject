from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.models.user import User
from app.services.supabase_service import supabase_service

router = APIRouter()


class CancellationRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.get("/me")
def get_current_user(user: User = Depends(require_current_user)) -> dict[str, Any]:
    return {
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "subscription_tier": user.subscription_tier,
        "is_active": user.is_active,
    }


@router.get("/subscription")
def get_subscription(user: User = Depends(require_current_user)) -> dict[str, Any]:
    subscription = supabase_service.get_subscription(user.email)
    if subscription:
        return subscription
    return {
        "subscription_tier": user.subscription_tier,
        "status": "ACTIVE",
        "billing_provider": "MANUAL",
        "current_period_end": None,
        "cancel_at_period_end": False,
    }


@router.post("/subscription/cancel")
def cancel_subscription(
    payload: CancellationRequest,
    user: User = Depends(require_current_user),
) -> dict[str, Any]:
    if user.subscription_tier != "ENTERPRISE":
        raise HTTPException(status_code=409, detail="Enterprise 구독만 취소할 수 있습니다.")
    return supabase_service.request_subscription_cancellation(user.email, payload.reason)
