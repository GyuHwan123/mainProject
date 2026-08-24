from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class User:
    """Application user hydrated from the Supabase public.users table."""

    id: str
    name: str
    email: str
    password_hash: str | None = None
    provider: str = "local"
    provider_id: str | None = None
    role: str = "USER"
    subscription_tier: str = "PERSONAL"
    is_active: bool = True

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "User":
        return cls(
            id=str(record.get("id") or ""),
            name=record.get("name") or record.get("email") or "User",
            email=(record.get("email") or "").lower(),
            password_hash=record.get("password_hash"),
            provider=record.get("social_provider") or "local",
            provider_id=record.get("social_id"),
            role=record.get("role") or "USER",
            subscription_tier=record.get("subscription_tier") or "PERSONAL",
            is_active=record.get("is_active", True),
        )
