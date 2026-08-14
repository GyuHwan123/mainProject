from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, index=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider: Mapped[str] = mapped_column("social_provider", String(30), default="local", nullable=False)
    provider_id: Mapped[str | None] = mapped_column("social_id", String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="USER", nullable=False)
    subscription_tier: Mapped[str] = mapped_column(String(20), default="PERSONAL", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
