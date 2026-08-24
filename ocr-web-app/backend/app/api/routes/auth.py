from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import create_access_token, decode_access_token, get_password_hash, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, SignupRequest, SocialLoginRequest
from app.services.supabase_service import supabase_service

router = APIRouter()
security = HTTPBearer()


def _user_from_email(email: str | None) -> User | None:
    if not email:
        return None
    record = supabase_service.get_user_by_email(email)
    return User.from_record(record) if record else None


def require_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 토큰입니다.") from exc
    user = _user_from_email(payload.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자를 찾을 수 없습니다.")
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


@router.post("/signup")
def signup(payload: SignupRequest) -> dict[str, str]:
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
    return {"message": "회원가입이 완료되었습니다.", "name": user.name, "email": user.email}


@router.post("/social-login", response_model=LoginResponse)
def social_login(payload: SocialLoginRequest) -> LoginResponse:
    if payload.provider.lower() != "supabase":
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="해당 소셜 제공자는 아직 준비 중입니다.")
    identity = supabase_service.get_user_from_token(payload.token)
    email = (identity.get("email") or "").lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Supabase 사용자 이메일을 확인할 수 없습니다.")
    existing = _user_from_email(email)
    if existing and existing.provider == "local":
        user = existing
    else:
        user = User.from_record(supabase_service.upsert_user(
            email=email, name=identity.get("name") or "Supabase User",
            provider=identity.get("provider") or "supabase", provider_id=identity.get("id"),
            role=existing.role if existing else "USER",
        ))
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="비활성화된 사용자입니다.")
    return LoginResponse(
        access_token=create_access_token(subject=user.email), user_email=user.email,
        user_name=user.name, user_role=user.role, user_subscription_tier=user.subscription_tier,
    )


@router.get("/me")
def get_me(user: User = Depends(require_current_user)) -> dict[str, str]:
    return {"email": user.email, "name": user.name, "role": user.role, "subscription_tier": user.subscription_tier}
