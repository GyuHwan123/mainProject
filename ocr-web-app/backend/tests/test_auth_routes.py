from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from app.api.routes import auth
from app.core.security import decode_access_token, get_password_hash
from app.schemas.auth import LoginRequest, OAuthExchangeRequest, SignupRequest


class BackendAuthRouteTests(TestCase):
    def test_signup_hashes_password_and_returns_app_session(self):
        created = {
            "id": "user-1",
            "name": "테스트 사용자",
            "email": "new@example.com",
            "password_hash": get_password_hash("Password!123"),
            "social_provider": "local",
            "social_id": "new@example.com",
            "role": "USER",
            "subscription_tier": "PERSONAL",
            "is_active": True,
        }
        with patch.object(auth.supabase_service, "get_user_by_email", return_value=None), patch.object(
            auth.supabase_service, "create_user", return_value=created
        ) as create_user:
            response = auth.signup(SignupRequest(name="테스트 사용자", email="NEW@example.com", password="Password!123"))

        self.assertEqual(response.user_email, "new@example.com")
        self.assertEqual(decode_access_token(response.access_token)["sub"], "new@example.com")
        self.assertNotEqual(create_user.call_args.kwargs["password_hash"], "Password!123")

    def test_signup_rejects_duplicate_email(self):
        with patch.object(auth.supabase_service, "get_user_by_email", return_value={"id": "existing"}):
            with self.assertRaises(HTTPException) as raised:
                auth.signup(SignupRequest(name="기존 사용자", email="old@example.com", password="Password!123"))
        self.assertEqual(raised.exception.status_code, 409)

    def test_login_returns_session_for_valid_backend_password(self):
        record = {
            "id": "admin-1",
            "name": "관리자",
            "email": "admin@example.com",
            "password_hash": get_password_hash("Password!123"),
            "social_provider": "local",
            "social_id": "admin@example.com",
            "role": "ADMIN",
            "subscription_tier": "PERSONAL",
            "is_active": True,
        }
        with patch.object(auth.supabase_service, "get_user_by_email", return_value=record):
            response = auth.login(LoginRequest(email="admin@example.com", password="Password!123"))
        self.assertEqual(response.user_role, "ADMIN")

    def test_login_rejects_invalid_password(self):
        record = {
            "id": "user-1",
            "name": "사용자",
            "email": "user@example.com",
            "password_hash": get_password_hash("Correct!123"),
            "is_active": True,
        }
        with patch.object(auth.supabase_service, "get_user_by_email", return_value=record):
            with self.assertRaises(HTTPException) as raised:
                auth.login(LoginRequest(email="user@example.com", password="WrongPass!123"))
        self.assertEqual(raised.exception.status_code, 401)

    def test_social_exchange_preserves_existing_local_account_role(self):
        record = {
            "id": "admin-1", "name": "관리자", "email": "admin@example.com",
            "password_hash": get_password_hash("Password!123"),
            "social_provider": "local", "social_id": "admin@example.com",
            "role": "ADMIN", "subscription_tier": "PERSONAL", "is_active": True,
        }
        identity = {"id": "google-id", "email": "admin@example.com", "name": "Google Admin", "provider": "google"}
        with patch.object(auth.supabase_service, "get_user_from_social_token", return_value=identity), patch.object(
            auth.supabase_service, "get_user_by_email", return_value=record
        ), patch.object(auth.supabase_service, "upsert_user") as upsert_user:
            response = auth.exchange_oauth_session(OAuthExchangeRequest(provider="supabase", token="social-token"))
        self.assertEqual(response.user_role, "ADMIN")
        upsert_user.assert_not_called()

    def test_social_exchange_creates_new_app_user(self):
        identity = {"id": "google-id", "email": "social@example.com", "name": "소셜 사용자", "provider": "google"}
        created = {
            "id": "user-2", "name": "소셜 사용자", "email": "social@example.com",
            "password_hash": None, "social_provider": "google", "social_id": "google-id",
            "role": "USER", "subscription_tier": "PERSONAL", "is_active": True,
        }
        with patch.object(auth.supabase_service, "get_user_from_social_token", return_value=identity), patch.object(
            auth.supabase_service, "get_user_by_email", return_value=None
        ), patch.object(auth.supabase_service, "upsert_user", return_value=created) as upsert_user:
            response = auth.exchange_oauth_session(OAuthExchangeRequest(provider="supabase", token="social-token"))
        self.assertEqual(response.user_email, "social@example.com")
        upsert_user.assert_called_once()
