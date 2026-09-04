from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.models.user import User
from app.services.supabase_service import supabase_service


ENTERPRISE_AMOUNT = 99_000
TOSS_CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"


class BillingService:
    def _db_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = httpx.request(
            method,
            f"{supabase_service.url}/rest/v1/{path}",
            headers={**supabase_service._service_headers(), **kwargs.pop("headers", {})},
            timeout=15,
            **kwargs,
        )
        supabase_service._raise_for_supabase(response, "결제 정보 처리 실패")
        return response

    def create_order(self, user: User, plan: str) -> dict[str, Any]:
        if plan != "ENTERPRISE":
            raise HTTPException(status_code=422, detail="지원하지 않는 요금제입니다.")

        pending = self._db_request(
            "GET",
            "payment_orders",
            params={
                "select": "*", "user_id": f"eq.{user.id}",
                "status": "eq.PENDING", "order": "created_at.desc", "limit": "1",
            },
        ).json()
        if pending:
            order = pending[0]
        else:
            payload = {
                "user_id": user.id,
                "order_id": f"enterprise-{uuid4().hex}",
                "plan": "ENTERPRISE",
                "amount": ENTERPRISE_AMOUNT,
                "currency": "KRW",
                "status": "PENDING",
            }
            response = self._db_request(
                "POST", "payment_orders",
                headers={"Prefer": "return=representation"}, json=payload,
            )
            order = response.json()[0]
        return {
            "orderId": order["order_id"],
            "amount": order["amount"],
            "currency": order.get("currency") or "KRW",
            "orderName": "Enterprise Workspace 30일 이용권",
            "customerName": user.name,
            "customerEmail": user.email,
            "customerKey": user.id,
        }

    def get_order(self, user: User, order_id: str) -> dict[str, Any]:
        rows = self._db_request(
            "GET", "payment_orders",
            params={"select": "*", "order_id": f"eq.{order_id}", "user_id": f"eq.{user.id}", "limit": "1"},
        ).json()
        if not rows:
            raise HTTPException(status_code=404, detail="결제 주문을 찾을 수 없습니다.")
        return rows[0]

    def mark_order(self, user: User, order_id: str, status: str, code: str | None, message: str | None) -> dict[str, Any]:
        order = self.get_order(user, order_id)
        if order["status"] == "PAID":
            return order
        response = self._db_request(
            "PATCH", "payment_orders",
            params={"id": f"eq.{order['id']}"},
            headers={"Prefer": "return=representation"},
            json={
                "status": status,
                "failure_code": (code or "")[:100] or None,
                "failure_message": (message or "")[:500] or None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return response.json()[0]

    def confirm_payment(self, user: User, payment_key: str, order_id: str, amount: int) -> dict[str, Any]:
        order = self.get_order(user, order_id)
        if order["status"] == "PAID":
            subscription = supabase_service.get_subscription(user.email)
            return {"status": "PAID", "subscription": subscription, "already_confirmed": True}
        if order["status"] != "PENDING":
            raise HTTPException(status_code=409, detail="승인할 수 없는 결제 주문입니다.")
        if order["plan"] != "ENTERPRISE" or int(order["amount"]) != ENTERPRISE_AMOUNT or amount != ENTERPRISE_AMOUNT:
            self.mark_order(user, order_id, "FAILED", "AMOUNT_MISMATCH", "결제 금액이 일치하지 않습니다.")
            raise HTTPException(status_code=400, detail="결제 금액이 일치하지 않습니다.")
        if not settings.TOSS_SECRET_KEY.startswith("test_gsk_"):
            raise HTTPException(status_code=503, detail="토스페이먼츠 테스트 시크릿 키 설정이 필요합니다.")

        encoded_key = base64.b64encode(f"{settings.TOSS_SECRET_KEY}:".encode()).decode()
        toss_response = httpx.post(
            TOSS_CONFIRM_URL,
            headers={
                "Authorization": f"Basic {encoded_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": str(order["id"]),
            },
            json={"paymentKey": payment_key, "orderId": order_id, "amount": amount},
            timeout=20,
        )
        if toss_response.status_code >= 400:
            error = toss_response.json() if toss_response.headers.get("content-type", "").startswith("application/json") else {}
            if toss_response.status_code < 500:
                self.mark_order(user, order_id, "FAILED", error.get("code"), error.get("message"))
            raise HTTPException(status_code=400 if toss_response.status_code < 500 else 502, detail=error.get("message") or "결제 승인에 실패했습니다.")

        payment = toss_response.json()
        if payment.get("orderId") != order_id or int(payment.get("totalAmount") or 0) != ENTERPRISE_AMOUNT or payment.get("status") != "DONE":
            self.mark_order(user, order_id, "FAILED", "INVALID_APPROVAL", "승인 결과 검증에 실패했습니다.")
            raise HTTPException(status_code=502, detail="결제 승인 결과를 검증하지 못했습니다.")

        approved_at = payment.get("approvedAt") or datetime.now(timezone.utc).isoformat()
        method = payment.get("method") or "토스페이먼츠"
        receipt_url = (payment.get("receipt") or {}).get("url")
        result = self._db_request(
            "POST", "rpc/complete_toss_payment",
            headers={"Prefer": "return=representation"},
            json={
                "p_user_id": user.id,
                "p_order_id": order_id,
                "p_payment_key": payment_key,
                "p_amount": ENTERPRISE_AMOUNT,
                "p_payment_method": method,
                "p_receipt_url": receipt_url,
                "p_approved_at": approved_at,
            },
        ).json()
        return {"status": "PAID", **result, "subscription_tier": "ENTERPRISE"}


billing_service = BillingService()
