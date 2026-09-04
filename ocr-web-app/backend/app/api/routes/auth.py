from datetime import datetime, timedelta, timezone
import hashlib
import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import create_access_token, decode_access_token, get_password_hash, verify_password
from app.models.user import User
from app.core.config import settings
from app.schemas.auth import (
    LoginRequest, LoginResponse, MessageResponse, OAuthExchangeRequest,
    PasswordResetConfirmRequest, PasswordResetRequest, SignupRequest,
)
from app.services.email_service import email_service
from app.services.supabase_service import supabase_service
from app.services.google_calendar_service import google_calendar_service

router = APIRouter()
security = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)
PASSWORD_RESET_MESSAGE = "가입된 이메일이라면 재설정 메일을 전송했습니다."


def _reset_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_from_email(email: str | None) -> User | None:
    if not email:
        return None
    record = supabase_service.get_user_by_email(email)
    return User.from_record(record) if record else None


def require_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> User:
    if not credentials or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 토큰이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않거나 만료된 토큰입니다.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = _user_from_email(payload.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    if not payload.email or not payload.password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="이메일과 비밀번호를 모두 입력해주세요.")
    if len(payload.password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="비밀번호는 최소 8자 이상이어야 합니다.")
    user = _user_from_email(payload.email.lower())
    if not user or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="비활성화된 사용자입니다.")
    return LoginResponse(
        access_token=create_access_token(subject=user.email), user_email=user.email,
        user_name=user.name, user_role=user.role, user_subscription_tier=user.subscription_tier,
    )


@router.post("/signup", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest) -> LoginResponse:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="이름을 입력해주세요.")
    if len(payload.password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="비밀번호는 최소 8자 이상이어야 합니다.")
    email = payload.email.lower()
    if supabase_service.get_user_by_email(email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 등록된 이메일입니다.")
    user = User.from_record(supabase_service.create_user(
        name=name, email=email, password_hash=get_password_hash(payload.password),
        provider="local", provider_id=email,
    ))
    return LoginResponse(
        access_token=create_access_token(subject=user.email), user_email=user.email,
        user_name=user.name, user_role=user.role, user_subscription_tier=user.subscription_tier,
    )


@router.post("/password-reset/request", response_model=MessageResponse)
def request_password_reset(payload: PasswordResetRequest) -> MessageResponse:
    user = _user_from_email(payload.email.lower())
    if user and user.password_hash and user.is_active:
        try:
            if not settings.FRONTEND_URL:
                raise RuntimeError("FRONTEND_URL is not configured")
            token = secrets.token_urlsafe(48)
            now = datetime.now(timezone.utc)
            supabase_service.invalidate_password_reset_tokens(user.id, now.isoformat())
            supabase_service.create_password_reset_token(
                user.id,
                _reset_token_hash(token),
                (now + timedelta(hours=1)).isoformat(),
            )
            reset_url = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={quote(token, safe='')}"
            email_service.send_password_reset(user.email, reset_url)
        except Exception:
            # The public response must not reveal account existence or mail configuration.
            # Never include the reset token or reset URL in logs.
            logger.exception("Password reset delivery failed for a registered account")
    return MessageResponse(message=PASSWORD_RESET_MESSAGE)


@router.post("/password-reset/confirm", response_model=MessageResponse)
def confirm_password_reset(payload: PasswordResetConfirmRequest) -> MessageResponse:
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="비밀번호는 최소 8자 이상이어야 합니다.")
    if len(payload.token) < 32:
        raise HTTPException(status_code=400, detail="유효하지 않거나 만료된 재설정 링크입니다.")
    confirmed = supabase_service.confirm_password_reset(
        _reset_token_hash(payload.token),
        get_password_hash(payload.new_password),
    )
    if not confirmed:
        raise HTTPException(status_code=400, detail="유효하지 않거나 만료된 재설정 링크입니다.")
    return MessageResponse(message="비밀번호가 변경되었습니다. 새 비밀번호로 로그인해 주세요.")


@router.post("/oauth/exchange", response_model=LoginResponse)
def exchange_oauth_session(payload: OAuthExchangeRequest) -> LoginResponse:
    if payload.provider.lower() != "supabase":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="지원하지 않는 소셜 인증 중개자입니다.")
    identity = supabase_service.get_user_from_social_token(payload.token)
    email = (identity.get("email") or "").lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="소셜 계정 이메일을 확인할 수 없습니다.")

    existing = _user_from_email(email)
    if existing:
        # 동일 이메일의 로컬 계정은 비밀번호 해시와 권한을 그대로 유지한다.
        user = existing
    else:
        user = User.from_record(supabase_service.upsert_user(
            email=email,
            name=identity.get("name") or "소셜 사용자",
            provider=identity.get("provider") or "social",
            provider_id=identity.get("id"),
            role="USER",
        ))
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="비활성화된 사용자입니다.")
    calendar_imported = 0
    calendar_sync_error = None
    if identity.get("provider") == "google" and payload.provider_access_token:
        try:
            calendar_imported = google_calendar_service.import_primary(email, payload.provider_access_token)
        except HTTPException as exc:
            calendar_sync_error = str(exc.detail)
        except Exception:
            calendar_sync_error = "Google Calendar 동기화 중 오류가 발생했습니다."
    return LoginResponse(
        access_token=create_access_token(subject=user.email), user_email=user.email,
        user_name=user.name, user_role=user.role, user_subscription_tier=user.subscription_tier,
        calendar_imported=calendar_imported, calendar_sync_error=calendar_sync_error,
    )


@router.get("/me")
def get_me(user: User = Depends(require_current_user)) -> dict[str, str]:
    return {"email": user.email, "name": user.name, "role": user.role, "subscription_tier": user.subscription_tier}
