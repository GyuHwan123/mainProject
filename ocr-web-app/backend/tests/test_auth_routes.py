from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from pydantic import ValidationError

from app.api.routes import auth
from app.api.routes import finance_evaluations, reports
from app.core.security import create_access_token, decode_access_token, get_password_hash
from app.core.config import Settings
from app.models.user import User
from app.schemas.auth import (
    LoginRequest, OAuthExchangeRequest, PasswordResetConfirmRequest,
    PasswordResetRequest, SignupRequest,
)


class BackendAuthRouteTests(TestCase):
    def test_jwt_settings_reject_unsafe_algorithm_and_expiry(self):
        with self.assertRaises(ValidationError):
            Settings(SECRET_KEY="x" * 48, ALGORITHM="none", _env_file=None)
        with self.assertRaises(ValidationError):
            Settings(SECRET_KEY="x" * 48, ACCESS_TOKEN_EXPIRE_MINUTES=0, _env_file=None)

    def test_cors_settings_reject_wildcard(self):
        with self.assertRaises(ValidationError):
            Settings(SECRET_KEY="x" * 48, CORS_ORIGINS=["*"], _env_file=None)

    @staticmethod
    def bearer(token: str, scheme: str = "Bearer") -> HTTPAuthorizationCredentials:
        return HTTPAuthorizationCredentials(scheme=scheme, credentials=token)

    def test_protected_dependency_rejects_missing_token_with_401(self):
        with self.assertRaises(HTTPException) as raised:
            auth.require_current_user(None)
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.headers, {"WWW-Authenticate": "Bearer"})

    def test_protected_dependency_rejects_malformed_authorization_scheme(self):
        with self.assertRaises(HTTPException) as raised:
            auth.require_current_user(self.bearer("credentials", scheme="Basic"))
        self.assertEqual(raised.exception.status_code, 401)

    def test_protected_dependency_rejects_invalid_jwt(self):
        with self.assertRaises(HTTPException) as raised:
            auth.require_current_user(self.bearer("not-a-valid-jwt"))
        self.assertEqual(raised.exception.status_code, 401)

    def test_protected_dependency_rejects_expired_jwt(self):
        expired = jwt.encode(
            {"sub": "user@example.com", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
            auth.settings.SECRET_KEY,
            algorithm=auth.settings.ALGORITHM,
        )
        with self.assertRaises(HTTPException) as raised:
            auth.require_current_user(self.bearer(expired))
        self.assertEqual(raised.exception.status_code, 401)

    def test_protected_dependency_rejects_inactive_user(self):
        record = {
            "id": "user-1", "name": "사용자", "email": "user@example.com",
            "password_hash": get_password_hash("Password!123"), "is_active": False,
        }
        with patch.object(auth.supabase_service, "get_user_by_email", return_value=record):
            with self.assertRaises(HTTPException) as raised:
                auth.require_current_user(self.bearer(create_access_token("user@example.com")))
        self.assertEqual(raised.exception.status_code, 401)

    def test_enterprise_report_rejects_personal_user(self):
        user = User(id="user-1", name="사용자", email="user@example.com", subscription_tier="PERSONAL")
        with self.assertRaises(HTTPException) as raised:
            reports.require_enterprise(user)
        self.assertEqual(raised.exception.status_code, 403)

    def test_developer_endpoint_rejects_roleless_developer_email(self):
        user = User(id="user-1", name="사용자", email="developer@docunex.com", role="USER")
        with self.assertRaises(HTTPException) as raised:
            finance_evaluations.require_developer(user)
        self.assertEqual(raised.exception.status_code, 403)

    def test_password_reset_request_does_not_reveal_unknown_email(self):
        with patch.object(auth.supabase_service, "get_user_by_email", return_value=None), patch.object(
            auth.email_service, "send_password_reset"
        ) as send_mail:
            response = auth.request_password_reset(PasswordResetRequest(email="missing@example.com"))

        self.assertEqual(response.message, auth.PASSWORD_RESET_MESSAGE)
        send_mail.assert_not_called()

    def test_password_reset_request_stores_only_hash_and_invalidates_old_tokens(self):
        record = {
            "id": "user-1", "name": "사용자", "email": "user@example.com",
            "password_hash": get_password_hash("OldPassword!123"), "is_active": True,
        }
        raw_token = "reset-token-that-is-long-enough-and-must-never-be-stored"
        with patch.object(auth.supabase_service, "get_user_by_email", return_value=record), patch.object(
            auth.supabase_service, "invalidate_password_reset_tokens"
        ) as invalidate, patch.object(
            auth.supabase_service, "create_password_reset_token"
        ) as create_token, patch.object(
            auth.email_service, "send_password_reset"
        ) as send_mail, patch.object(
            auth.secrets, "token_urlsafe", return_value=raw_token
        ), patch.object(auth.settings, "FRONTEND_URL", "https://app.example.com"):
            response = auth.request_password_reset(PasswordResetRequest(email="USER@example.com"))

        self.assertEqual(response.message, auth.PASSWORD_RESET_MESSAGE)
        invalidate.assert_called_once()
        stored_hash = create_token.call_args.args[1]
        self.assertEqual(stored_hash, auth._reset_token_hash(raw_token))
        self.assertNotEqual(stored_hash, raw_token)
        self.assertNotIn(raw_token, str(create_token.call_args))
        self.assertIn(raw_token, send_mail.call_args.args[1])

    def test_password_reset_confirm_hashes_new_password(self):
        raw_token = "valid-reset-token-that-is-at-least-thirty-two-characters"
        with patch.object(auth.supabase_service, "confirm_password_reset", return_value=True) as confirm:
            response = auth.confirm_password_reset(PasswordResetConfirmRequest(
                token=raw_token, new_password="NewPassword!123",
            ))

        self.assertIn("변경", response.message)
        self.assertEqual(confirm.call_args.args[0], auth._reset_token_hash(raw_token))
        self.assertNotEqual(confirm.call_args.args[1], "NewPassword!123")

    def test_password_reset_confirm_rejects_expired_or_used_token(self):
        raw_token = "invalid-reset-token-that-is-at-least-thirty-two-characters"
        with patch.object(auth.supabase_service, "confirm_password_reset", return_value=False):
            with self.assertRaises(HTTPException) as raised:
                auth.confirm_password_reset(PasswordResetConfirmRequest(
                    token=raw_token, new_password="NewPassword!123",
                ))
        self.assertEqual(raised.exception.status_code, 400)

    def test_password_reset_confirm_keeps_existing_password_policy(self):
        with patch.object(auth.supabase_service, "confirm_password_reset") as confirm:
            with self.assertRaises(HTTPException) as raised:
                auth.confirm_password_reset(PasswordResetConfirmRequest(
                    token="valid-reset-token-that-is-at-least-thirty-two-characters",
                    new_password="short",
                ))
        self.assertEqual(raised.exception.status_code, 400)
        confirm.assert_not_called()

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

    def test_login_rejects_inactive_user(self):
        record = {
            "id": "user-1", "name": "사용자", "email": "user@example.com",
            "password_hash": get_password_hash("Password!123"), "is_active": False,
        }
        with patch.object(auth.supabase_service, "get_user_by_email", return_value=record):
            with self.assertRaises(HTTPException) as raised:
                auth.login(LoginRequest(email="user@example.com", password="Password!123"))
        self.assertEqual(raised.exception.status_code, 403)

    def test_social_login_app_jwt_accesses_protected_dependency(self):
        created = {
            "id": "user-2", "name": "소셜 사용자", "email": "social@example.com",
            "password_hash": None, "social_provider": "google", "social_id": "google-id",
            "role": "USER", "subscription_tier": "PERSONAL", "is_active": True,
        }
        identity = {"id": "google-id", "email": "social@example.com", "name": "소셜 사용자", "provider": "google"}
        with patch.object(auth.supabase_service, "get_user_from_social_token", return_value=identity), patch.object(
            auth.supabase_service, "get_user_by_email", return_value=None
        ), patch.object(auth.supabase_service, "upsert_user", return_value=created):
            session = auth.exchange_oauth_session(OAuthExchangeRequest(provider="supabase", token="social-token"))
        with patch.object(auth.supabase_service, "get_user_by_email", return_value=created):
            protected_user = auth.require_current_user(self.bearer(session.access_token))
        self.assertEqual(protected_user.email, "social@example.com")

    def test_password_reset_allows_login_with_new_password(self):
        record = {
            "id": "user-1", "name": "사용자", "email": "user@example.com",
            "password_hash": get_password_hash("OldPassword!123"), "is_active": True,
        }

        def apply_password_reset(_token_hash, new_hash):
            record["password_hash"] = new_hash
            return True

        with patch.object(auth.supabase_service, "confirm_password_reset", side_effect=apply_password_reset):
            auth.confirm_password_reset(PasswordResetConfirmRequest(
                token="valid-reset-token-that-is-at-least-thirty-two-characters",
                new_password="NewPassword!123",
            ))
        with patch.object(auth.supabase_service, "get_user_by_email", return_value=record):
            response = auth.login(LoginRequest(email="user@example.com", password="NewPassword!123"))
        self.assertEqual(response.user_email, "user@example.com")

    def test_password_reset_does_not_revoke_existing_app_jwt(self):
        record = {
            "id": "user-1", "name": "사용자", "email": "user@example.com",
            "password_hash": get_password_hash("OldPassword!123"), "is_active": True,
        }
        existing_token = create_access_token("user@example.com")

        def apply_password_reset(_token_hash, new_hash):
            record["password_hash"] = new_hash
            return True

        with patch.object(auth.supabase_service, "confirm_password_reset", side_effect=apply_password_reset):
            auth.confirm_password_reset(PasswordResetConfirmRequest(
                token="valid-reset-token-that-is-at-least-thirty-two-characters",
                new_password="NewPassword!123",
            ))
        with patch.object(auth.supabase_service, "get_user_by_email", return_value=record):
            protected_user = auth.require_current_user(self.bearer(existing_token))
        self.assertEqual(protected_user.email, "user@example.com")

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

    def test_google_social_exchange_imports_calendar_without_changing_login(self):
        record = {
            "id": "user-3", "name": "Google 사용자", "email": "google@example.com",
            "password_hash": None, "social_provider": "google", "social_id": "google-id",
            "role": "USER", "subscription_tier": "PERSONAL", "is_active": True,
        }
        identity = {"id": "google-id", "email": "google@example.com", "name": "Google 사용자", "provider": "google"}
        with patch.object(auth.supabase_service, "get_user_from_social_token", return_value=identity), patch.object(
            auth.supabase_service, "get_user_by_email", return_value=record
        ), patch.object(auth.google_calendar_service, "import_primary", return_value=3) as import_primary:
            response = auth.exchange_oauth_session(OAuthExchangeRequest(
                provider="supabase", token="social-token", provider_access_token="google-token"
            ))
        self.assertEqual(response.calendar_imported, 3)
        self.assertIsNone(response.calendar_sync_error)
        import_primary.assert_called_once_with("google@example.com", "google-token")
