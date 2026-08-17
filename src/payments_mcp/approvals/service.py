"""Approval lifecycle service.

All state transitions use optimistic concurrency through ``ApprovalRepository.transition``.  This
keeps double-clicks and competing approvers from creating more than one approved authorization.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from .models import ApprovalAction, ApprovalDecision, ApprovalRequest, ApprovalStatus
from .repository import ApprovalRepository


class ApprovalService:
    def __init__(
        self, repository: ApprovalRepository, *, ttl_seconds: int = 900, system_principal_id: str = "approval-service"
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("approval ttl must be positive")
        self._repository = repository
        self._ttl = timedelta(seconds=ttl_seconds)
        self._system_principal_id = system_principal_id

    def request_payment(
        self,
        *,
        tenant_id: str,
        requester_principal_id: str,
        payer: str,
        payee: str,
        currency: str,
        amount_minor: int,
    ) -> ApprovalRequest:
        now = datetime.now(timezone.utc)
        return self._repository.create(
            ApprovalRequest(
                approval_id="appr-" + uuid.uuid4().hex,
                tenant_id=tenant_id,
                requester_principal_id=requester_principal_id,
                action=ApprovalAction.CREATE_PAYMENT,
                payer=payer,
                payee=payee,
                currency=currency,
                amount_minor=amount_minor,
                updated_at=now,
                updated_by_principal_id=requester_principal_id,
                expires_at=now + self._ttl,
            )
        )

    def request_refund(
        self,
        *,
        tenant_id: str,
        requester_principal_id: str,
        payment_id: str,
        currency: str,
        amount_minor: int,
    ) -> ApprovalRequest:
        now = datetime.now(timezone.utc)
        return self._repository.create(
            ApprovalRequest(
                approval_id="appr-" + uuid.uuid4().hex,
                tenant_id=tenant_id,
                requester_principal_id=requester_principal_id,
                action=ApprovalAction.REFUND_PAYMENT,
                payment_id=payment_id,
                currency=currency,
                amount_minor=amount_minor,
                updated_at=now,
                updated_by_principal_id=requester_principal_id,
                expires_at=now + self._ttl,
            )
        )

    def get(self, approval_id: str) -> ApprovalRequest | None:
        approval = self._repository.get(approval_id)
        return self._expire_if_needed(approval) if approval is not None else None

    def pending(self, tenant_id: str) -> list[ApprovalRequest]:
        return [self._expire_if_needed(approval) for approval in self._repository.list_pending(tenant_id)]

    def approve(self, approval_id: str, *, approver_principal_id: str) -> ApprovalRequest:
        approval = self._require_pending(approval_id)
        now = datetime.now(timezone.utc)
        updated = approval.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decision": ApprovalDecision.APPROVED,
                "decided_at": now,
                "decided_by_principal_id": approver_principal_id,
                "updated_at": now,
                "updated_by_principal_id": approver_principal_id,
            }
        )
        return self._repository.transition(
            approval_id,
            expected=ApprovalStatus.PENDING,
            expected_version=approval.version,
            updated=updated,
        )

    def reject(self, approval_id: str, *, approver_principal_id: str) -> ApprovalRequest:
        approval = self._require_pending(approval_id)
        now = datetime.now(timezone.utc)
        updated = approval.model_copy(
            update={
                "status": ApprovalStatus.REJECTED,
                "decision": ApprovalDecision.REJECTED,
                "decided_at": now,
                "decided_by_principal_id": approver_principal_id,
                "updated_at": now,
                "updated_by_principal_id": approver_principal_id,
            }
        )
        return self._repository.transition(
            approval_id,
            expected=ApprovalStatus.PENDING,
            expected_version=approval.version,
            updated=updated,
        )

    def consume(
        self, approval_id: str, *, mandate_id: str, consumed_by_principal_id: str | None = None
    ) -> ApprovalRequest:
        """Mark an approved authorization used exactly once before executing its action."""
        approval = self.get(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        if approval.status != ApprovalStatus.APPROVED:
            raise ValueError(f"approval is {approval.status}")
        updated = approval.model_copy(
            update={
                "status": ApprovalStatus.CONSUMED,
                "consumed_at": datetime.now(timezone.utc),
                "mandate_id": mandate_id,
                "updated_at": datetime.now(timezone.utc),
                "updated_by_principal_id": consumed_by_principal_id or self._system_principal_id,
            }
        )
        return self._repository.transition(
            approval_id,
            expected=ApprovalStatus.APPROVED,
            expected_version=approval.version,
            updated=updated,
        )

    def _require_pending(self, approval_id: str) -> ApprovalRequest:
        approval = self.get(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError(f"approval is {approval.status}")
        return approval

    def _expire_if_needed(self, approval: ApprovalRequest) -> ApprovalRequest:
        if approval.status != ApprovalStatus.PENDING or approval.expires_at > datetime.now(timezone.utc):
            return approval
        updated = approval.model_copy(
            update={
                "status": ApprovalStatus.EXPIRED,
                "updated_at": datetime.now(timezone.utc),
                "updated_by_principal_id": self._system_principal_id,
            }
        )
        return self._repository.transition(
            approval.approval_id,
            expected=ApprovalStatus.PENDING,
            expected_version=approval.version,
            updated=updated,
        )
