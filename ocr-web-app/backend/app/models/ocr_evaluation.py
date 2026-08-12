from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OCREvaluation(Base):
    __tablename__ = "ocr_evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(String(100), index=True)
    user_email: Mapped[str] = mapped_column(String(255), index=True)
    document_name: Mapped[str] = mapped_column(String(255))
    extracted_text: Mapped[str] = mapped_column(Text)
    ground_truth: Mapped[str] = mapped_column(Text)
    processing_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision: Mapped[float] = mapped_column(Float)
    recall: Mapped[float] = mapped_column(Float)
    f1_score: Mapped[float] = mapped_column(Float)
    true_positive: Mapped[int] = mapped_column(Integer)
    false_positive: Mapped[int] = mapped_column(Integer)
    false_negative: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
