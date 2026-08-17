"""Durable approval state and request invariants.

An approval describes one exact consequential operation.  It is deliberately independent from
the MCP transport so the approval UI/service can enforce a human authorization boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator


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
    """A request awaiting a named human approver's decision.

    Payment approvals bind both accounts. Refund approvals bind the original payment; this
    prevents a valid approval from being replayed for another resource.
    """

    approval_id: str
    tenant_id: str
    requester_principal_id: str
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
    mandate_id: str | None = None
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
