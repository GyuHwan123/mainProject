from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, decode_access_token, get_password_hash, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, SignupRequest, SocialLoginRequest
from app.services.supabase_service import supabase_service

router = APIRouter()
security = HTTPBearer()


def require_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 토큰입니다.") from exc

    user = db.query(User).filter(User.email == payload.get("sub")).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자를 찾을 수 없습니다.")
    return user


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    if not payload.email or not payload.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이메일과 비밀번호를 모두 입력해주세요.",
        )

    if len(payload.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="비밀번호는 최소 8자 이상이어야 합니다.",
        )

    email = payload.email.lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )

    try:
        supabase_service.upsert_user(
            email=user.email,
            provider=user.provider,
            provider_id=user.provider_id,
            role=user.role,
        )
    except HTTPException:
        # Local authentication remains available during a temporary Supabase
        # schema or network problem. Document upload will still report it.
        pass

    token = create_access_token(subject=user.email)
    return LoginResponse(
        access_token=token,
        user_email=user.email,
        user_name=user.name,
        user_role=user.role,
    )


@router.post("/signup")
def signup(payload: SignupRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    if not payload.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이름을 입력해주세요.",
        )

    if len(payload.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="비밀번호는 최소 8자 이상이어야 합니다.",
        )

    email = payload.email.lower()
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 등록된 이메일입니다.",
        )

    user = User(
        name=payload.name.strip(),
        email=email,
        password_hash=get_password_hash(payload.password),
        provider="local",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "message": "회원가입이 완료되었습니다.",
        "name": user.name,
        "email": user.email,
    }


@router.post("/social-login", response_model=LoginResponse)
def social_login(payload: SocialLoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    provider = payload.provider.lower()

    if provider == "supabase":
        supabase_user = supabase_service.get_user_from_token(payload.token)
        email = (supabase_user.get("email") or "").lower()
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Supabase 사용자 이메일을 확인할 수 없습니다.",
            )

        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                name=supabase_user.get("name") or "Supabase User",
                email=email,
                provider=supabase_user.get("provider") or "supabase",
                provider_id=supabase_user.get("id"),
                password_hash=None,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        elif user.provider != "local":
            user.provider = supabase_user.get("provider") or "supabase"
            user.provider_id = supabase_user.get("id")
            db.commit()

        supabase_service.upsert_user(
            email=user.email,
            provider=supabase_user.get("provider") or "supabase",
            provider_id=supabase_user.get("id"),
            role=user.role,
        )

        token = create_access_token(subject=user.email)
        return LoginResponse(
            access_token=token,
            user_email=user.email,
            user_name=user.name,
            user_role=user.role,
        )

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="해당 소셜 제공자는 아직 준비 중입니다. Supabase, Google, Apple 연동은 다음 단계에서 활성화됩니다.",
    )


@router.get("/me")
def get_me(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 토큰입니다.",
        ) from exc

    email = payload.get("sub")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다.",
        )

    return {"email": user.email, "name": user.name, "role": user.role}
