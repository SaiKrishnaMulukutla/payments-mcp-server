"""Core immutable values and persistence-facing domain models."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PaymentState(StrEnum):
    CREATED = "created"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class BillAmount(BaseModel):
    model_config = ConfigDict(frozen=True)
    amount_paise: int = Field(gt=0)
    currency: str = Field(default="INR", pattern="^INR$")


class FetchedBillSnapshot(BaseModel):
    """Immutable provider response facts used to create an intent; amount is never model input."""

    model_config = ConfigDict(frozen=True)
    fetch_ref_id: UUID
    registered_account_id: UUID
    biller_id: str
    amount_paise: int = Field(gt=0)
    customer_name: str | None = None
    bill_number: str | None = None
    due_at: datetime | None = None
    fetched_at: datetime
    expires_at: datetime
    provider_payload: dict[str, object]
    snapshot_sha256: str = Field(pattern="^[a-f0-9]{64}$")


class PaymentIntent(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    fetch_ref_id: UUID
    registered_account_id: UUID
    amount_paise: int = Field(gt=0)
    fee_paise: int = Field(default=0, ge=0)
    state: PaymentState
    created_at: datetime


class PaymentStatus(BaseModel):
    model_config = ConfigDict(frozen=True)
    intent_id: UUID
    state: PaymentState
    amount_paise: int
    currency: str = "INR"
    provider_reference: str | None = None
    updated_at: datetime
