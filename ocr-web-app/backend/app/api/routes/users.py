from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

router = APIRouter()


class UserProfile(BaseModel):
    email: EmailStr
    plan: str
    usage_count: int
    history_count: int


@router.get("/me", response_model=UserProfile)
def get_current_user() -> UserProfile:
    return UserProfile(
        email="user@example.com",
        plan="Pro",
        usage_count=184,
        history_count=32,
    )


@router.get("/history")
def get_user_history() -> list[dict[str, str]]:
    return [
        {"name": "프로젝트 발표자료", "date": "2026-08-10"},
        {"name": "영수증 정리", "date": "2026-08-08"},
    ]
