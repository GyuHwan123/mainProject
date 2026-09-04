from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.models.user import User
from app.services.billing_service import BillingService, ENTERPRISE_AMOUNT


def test_create_order_rejects_unknown_plan() -> None:
    service = BillingService()
    user = User(id="user-1", name="사용자", email="user@example.com")
    with pytest.raises(HTTPException) as error:
        service.create_order(user, "FREE")
    assert error.value.status_code == 422


def test_confirm_rejects_client_amount_before_toss(monkeypatch: pytest.MonkeyPatch) -> None:
    service = BillingService()
    user = User(id="user-1", name="사용자", email="user@example.com")
    monkeypatch.setattr(service, "get_order", lambda *_: {
        "id": "order-row", "status": "PENDING", "plan": "ENTERPRISE", "amount": ENTERPRISE_AMOUNT,
    })
    marked = Mock()
    monkeypatch.setattr(service, "mark_order", marked)

    with pytest.raises(HTTPException) as error:
        service.confirm_payment(user, "payment-key-long-enough", "enterprise-order", 100)

    assert error.value.status_code == 400
    marked.assert_called_once()


def test_confirm_activates_only_after_verified_toss_response(monkeypatch: pytest.MonkeyPatch) -> None:
    service = BillingService()
    user = User(id="user-1", name="사용자", email="user@example.com")
    monkeypatch.setattr(settings, "TOSS_SECRET_KEY", "test_gsk_example")
    monkeypatch.setattr(service, "get_order", lambda *_: {
        "id": "order-row", "status": "PENDING", "plan": "ENTERPRISE", "amount": ENTERPRISE_AMOUNT,
    })
    toss_response = Mock(status_code=200, headers={"content-type": "application/json"})
    toss_response.json.return_value = {
        "orderId": "enterprise-order", "totalAmount": ENTERPRISE_AMOUNT, "status": "DONE",
        "approvedAt": "2026-09-04T12:00:00+09:00", "method": "카드", "receipt": {"url": "https://example.test/receipt"},
    }
    monkeypatch.setattr("app.services.billing_service.httpx.post", lambda *args, **kwargs: toss_response)
    rpc_response = Mock()
    rpc_response.json.return_value = {"subscription": {"status": "ACTIVE"}, "payment": {"status": "PAID"}}
    db_request = Mock(return_value=rpc_response)
    monkeypatch.setattr(service, "_db_request", db_request)

    result = service.confirm_payment(user, "payment-key-long-enough", "enterprise-order", ENTERPRISE_AMOUNT)

    assert result["status"] == "PAID"
    assert result["subscription_tier"] == "ENTERPRISE"
    assert db_request.call_args.args[:2] == ("POST", "rpc/complete_toss_payment")
