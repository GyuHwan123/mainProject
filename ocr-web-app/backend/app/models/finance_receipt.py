from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


DocumentType = Literal["EXPENSE_REPORT", "TRAVEL_EXPENSE", "PURCHASE_REQUEST", "WELFARE_BENEFIT"]
PaymentMethod = Literal["카드", "현금", "기타"]


class FinanceClassifyRequest(BaseModel):
    document_id: str
    source_file_name: str | None = Field(default=None, max_length=500)
    save_to_archive: bool = True


class FinanceExportRequest(BaseModel):
    record_ids: list[str] = Field(min_length=1, max_length=200)


class FinanceRecordUpdate(BaseModel):
    document_type: DocumentType
    expense_category: str = Field(min_length=1, max_length=100)
    merchant: str | None = Field(default=None, max_length=200)
    transaction_date: date | None = None
    supply_amount: float | None = Field(default=None, ge=0)
    tax_amount: float | None = Field(default=None, ge=0)
    total_amount: float = Field(default=0, ge=0)
    payment_method: PaymentMethod | None = None
    description: str | None = Field(default=None, max_length=1000)
    items: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    status: Literal["REVIEW", "CONFIRMED"] = "CONFIRMED"


class FinanceRecord(BaseModel):
    id: str
    document_id: str
    document_type: DocumentType | None
    expense_category: str | None
    merchant: str | None = None
    transaction_date: date | None = None
    supply_amount: float | None = None
    tax_amount: float | None = None
    total_amount: float = 0
    payment_method: str | None = None
    description: str | None = None
    structured_data: dict[str, Any] = Field(default_factory=dict)
    model_name: str
    prompt_version: str | None = None
    duplicate_of_record_id: str | None = None
    processed_at: datetime | None = None
    status: str
    created_at: datetime
