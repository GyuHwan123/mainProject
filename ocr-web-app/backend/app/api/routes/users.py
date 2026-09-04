from typing import Any
from datetime import datetime, timezone
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.models.user import User
from app.services.supabase_service import supabase_service
from app.services.dashboard_service import dashboard_service
from app.core.security import get_password_hash, verify_password

router = APIRouter()

PLAN_MONTHLY_AMOUNTS = {"FREE": 0, "ENTERPRISE": 99_000}


def _with_plan_amount(subscription: dict[str, Any]) -> dict[str, Any]:
    tier = str(subscription.get("subscription_tier") or "FREE").upper()
    return {**subscription, "monthly_amount": PLAN_MONTHLY_AMOUNTS.get(tier, 0)}


class CancellationRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)

class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)

class AccountDeletion(BaseModel):
    password: str | None = Field(default=None, max_length=200)
    confirmation: str



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
        return _with_plan_amount(subscription)
    return _with_plan_amount({
        "subscription_tier": user.subscription_tier,
        "status": "ACTIVE",
        "billing_provider": "MANUAL",
        "current_period_end": None,
        "cancel_at_period_end": False,
    })


@router.post("/subscription/cancel")
def cancel_subscription(
    payload: CancellationRequest,
    user: User = Depends(require_current_user),
) -> dict[str, Any]:
    if user.subscription_tier != "ENTERPRISE":
        raise HTTPException(status_code=409, detail="Enterprise 구독만 취소할 수 있습니다.")
    return _with_plan_amount(supabase_service.request_subscription_cancellation(user.email, payload.reason))

@router.patch("/me")
def update_profile(payload: ProfileUpdate, user: User = Depends(require_current_user)) -> dict[str, Any]:
    row = supabase_service.update_user_account(user.email, {"name": payload.name.strip()})
    return {"email": row["email"], "name": row.get("name"), "role": row.get("role"), "subscription_tier": row.get("subscription_tier"), "is_active": row.get("is_active", True)}

@router.post("/password")
def change_password(payload: PasswordChange, user: User = Depends(require_current_user)) -> dict[str, str]:
    row = supabase_service.get_user_by_email(user.email)
    if not row or not row.get("password_hash"):
        raise HTTPException(status_code=409, detail="소셜 로그인 계정은 연결된 로그인 제공자에서 비밀번호를 변경해 주세요.")
    if not verify_password(payload.current_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 일치하지 않습니다.")
    supabase_service.update_user_account(user.email, {"password_hash": get_password_hash(payload.new_password)})
    return {"message": "비밀번호가 변경되었습니다."}

@router.delete("/me")
def delete_account(payload: AccountDeletion, user: User = Depends(require_current_user)) -> Response:
    if payload.confirmation.strip() != "계정 탈퇴":
        raise HTTPException(status_code=400, detail="확인 문구를 정확히 입력해 주세요.")
    row = supabase_service.get_user_by_email(user.email)
    if row and row.get("password_hash") and (not payload.password or not verify_password(payload.password, row["password_hash"])):
        raise HTTPException(status_code=400, detail="비밀번호가 일치하지 않습니다.")
    supabase_service.update_user_account(user.email, {"is_active": False})
    return Response(status_code=204)

@router.get("/data-export")
def export_user_data(user: User = Depends(require_current_user)) -> Response:
    activity = supabase_service.list_business_activity(user.email)
    payload = {"exported_at": datetime.now(timezone.utc).isoformat(), "profile": get_current_user(user), "subscription": get_subscription(user), "documents": supabase_service.list_ocr_documents(user.email), "rag_documents": supabase_service.list_rag_documents(user.email), "chat_sessions": supabase_service.list_chat_sessions(user.email), "knowledge_scraps": supabase_service.list_knowledge_scraps(user.email), "finance_records": supabase_service.list_finance_records(user.email, limit=1000), "rag_questions": activity["rag_questions"], "agent_logs": activity["agent_logs"], "schedules": [item.model_dump() for item in dashboard_service.list_schedules(user.email)], "tasks": [item.model_dump() for item in dashboard_service.list_tasks(user.email)], "meetings": [item.model_dump() for item in dashboard_service.list_meetings(user.email)]}
    body = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    return Response(content=body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="account-data-{datetime.now().date().isoformat()}.json"'})

@router.post("/subscription/cancel/revoke")
def revoke_cancellation(user: User = Depends(require_current_user)) -> dict[str, Any]:
    return _with_plan_amount(supabase_service.revoke_subscription_cancellation(user.email))

@router.get("/billing-history")
def billing_history(user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    return supabase_service.list_billing_history(user.email)
