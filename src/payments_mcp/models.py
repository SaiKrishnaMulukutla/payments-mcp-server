"""Shared Pydantic models across the flat payments-mcp modules.

Consolidates the data models previously spread across ``mandates/``, ``approvals/``,
``operations/`` and ``backend/`` into a single module so the domain speaks one vocabulary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Mandate domain
# ---------------------------------------------------------------------------


class MandateOperation(str, Enum):
    PAY_BILL = "pay_bill"


class AggregatePeriod(str, Enum):
    MANDATE_LIFETIME = "MANDATE_LIFETIME"
    MONTHLY = "MONTHLY"


class MandateStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    EXHAUSTED = "EXHAUSTED"


class MandateTerms(BaseModel):
    """Immutable delegated authority shown to a human before signing."""

    payer_account_id: str = Field(min_length=1)
    allowed_payee_ids: list[str] = Field(min_length=1)
    allowed_operations: list[MandateOperation] = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    purpose: str = Field(min_length=1, max_length=120)
    per_transaction_limit_minor: int = Field(gt=0)
    aggregate_limit_minor: int = Field(gt=0)
    aggregate_period: AggregatePeriod = AggregatePeriod.MANDATE_LIFETIME
    approval_required_above_minor: int = Field(ge=0)
    valid_from: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_bounds(self) -> "MandateTerms":
        if self.expires_at <= self.valid_from:
            raise ValueError("mandate expiry must be after its valid-from time")
        if self.aggregate_limit_minor < self.per_transaction_limit_minor:
            raise ValueError("aggregate limit must be at least the per-transaction limit")
        if self.approval_required_above_minor > self.per_transaction_limit_minor:
            raise ValueError("approval threshold cannot exceed the per-transaction limit")
        if len(set(self.allowed_payee_ids)) != len(self.allowed_payee_ids):
            raise ValueError("allowed payee ids must be unique")
        return self


class ReusableMandate(BaseModel):
    """Persisted, signed delegated authority; distinct from a payment operation."""

    mandate_id: str
    proposal_id: str
    tenant_id: str
    requester_principal_id: str
    terms: MandateTerms
    mandate_status: MandateStatus
    signed_artifact: str
    signing_key_id: str
    issued_at: datetime
    revoked_at: datetime | None = None


class Mandate(BaseModel):
    """Legacy single-payment-compatible token payload."""

    mandate_id: str
    payer: str
    payee: str
    currency: str
    max_amount_minor: int


# ---------------------------------------------------------------------------
# Proposal + approval domain
# ---------------------------------------------------------------------------


class ProposalApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class MandateProposal(BaseModel):
    """Untrusted agent-proposed authority awaiting a human decision."""

    proposal_id: str
    tenant_id: str
    requester_principal_id: str
    terms: MandateTerms
    approval_status: ProposalApprovalStatus = ProposalApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: datetime | None = None
    decided_by_principal_id: str | None = None
    updated_at: datetime | None = None
    updated_by_principal_id: str | None = None
    version: int = Field(default=0, ge=0)


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"


class ApprovalAction(str, Enum):
    CREATE_PAYMENT = "create_payment"
    REFUND_PAYMENT = "refund_payment"


class ApprovalDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalRequest(BaseModel):
    """A request awaiting a named human approver's decision."""

    approval_id: str
    tenant_id: str
    requester_principal_id: str
    mandate_id: str
    operation_id: str
    action: ApprovalAction
    amount_minor: int = Field(gt=0)
    currency: str
    payer: str | None = None
    payee: str | None = None
    payment_id: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision: ApprovalDecision | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by_principal_id: str | None = None
    updated_at: datetime | None = None
    updated_by_principal_id: str | None = None
    consumed_at: datetime | None = None
    version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_action_binding(self) -> "ApprovalRequest":
        if self.action == ApprovalAction.CREATE_PAYMENT:
            if not self.payer or not self.payee or self.payment_id is not None:
                raise ValueError("payment approval requires payer and payee only")
        elif self.action == ApprovalAction.REFUND_PAYMENT:
            if not self.payment_id or self.payer is not None or self.payee is not None:
                raise ValueError("refund approval requires payment_id only")
        return self


# ---------------------------------------------------------------------------
# Payment-operation domain
# ---------------------------------------------------------------------------


class OperationState(str, Enum):
    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    RESERVED = "RESERVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"


class PaymentOperation(BaseModel):
    """One exercise of an active reusable mandate; never the mandate itself."""

    operation_id: str
    tenant_id: str
    mandate_id: str
    requester_principal_id: str
    payer_account_id: str
    payee_account_id: str
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    state: OperationState = OperationState.CREATED
    idempotency_key: str
    operation_approval_id: str | None = None
    provider_payment_id: str | None = None
    provider_transaction_id: str | None = None
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Backend DTOs
# ---------------------------------------------------------------------------


class Payment(BaseModel):
    id: str
    status: str
    amount_minor: int
    currency: str
    created_at: str


class Refund(BaseModel):
    id: str
    payment_id: str
    amount_minor: int
    status: str
    created_at: str


class Balance(BaseModel):
    account_id: str
    balance_minor: int
    currency: str
    updated_at: str


class LedgerEntry(BaseModel):
    transaction_id: str
    direction: str  # DEBIT | CREDIT
    amount_minor: int
    created_at: str


class LedgerPage(BaseModel):
    account_id: str
    count: int
    entries: list[LedgerEntry]


class IntegrityReport(BaseModel):
    balanced: bool
    ledger_net: int
    drifted_accounts: int


__all__ = [
    "AggregatePeriod",
    "ApprovalAction",
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalStatus",
    "Balance",
    "IntegrityReport",
    "LedgerEntry",
    "LedgerPage",
    "Mandate",
    "MandateOperation",
    "MandateProposal",
    "MandateStatus",
    "MandateTerms",
    "OperationState",
    "Payment",
    "PaymentOperation",
    "ProposalApprovalStatus",
    "Refund",
    "ReusableMandate",
]