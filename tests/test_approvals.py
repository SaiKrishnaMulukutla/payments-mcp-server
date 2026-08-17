"""Approval-domain tests independent of the HTTP transport and database driver."""

from datetime import datetime, timedelta, timezone

import pytest

from payments_mcp.approvals import ApprovalAction, ApprovalDecision, ApprovalRequest, ApprovalService, ApprovalStatus


class FakeApprovalRepository:
    """Test double; production has no process-local approval repository."""

    def __init__(self) -> None:
        self._approvals: dict[str, ApprovalRequest] = {}

    def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        self._approvals[approval.approval_id] = approval
        return approval

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._approvals.get(approval_id)

    def list_pending(self, tenant_id: str) -> list[ApprovalRequest]:
        return [
            approval
            for approval in self._approvals.values()
            if approval.tenant_id == tenant_id and approval.status == ApprovalStatus.PENDING
        ]

    def transition(
        self,
        approval_id: str,
        *,
        expected: ApprovalStatus,
        expected_version: int,
        updated: ApprovalRequest,
    ) -> ApprovalRequest:
        current = self._approvals[approval_id]
        if current.status != expected or current.version != expected_version:
            raise ValueError("approval state changed; reload before retrying")
        persisted = updated.model_copy(update={"version": current.version + 1})
        self._approvals[approval_id] = persisted
        return persisted


def _service() -> ApprovalService:
    return ApprovalService(FakeApprovalRepository())


def test_payment_approval_records_human_approver_and_consumes_once():
    service = _service()
    request = service.request_payment(
        tenant_id="tenant-a",
        requester_principal_id="agent-a",
        payer="acct-A",
        payee="acct-B",
        currency="INR",
        amount_minor=5000,
    )

    approved = service.approve(request.approval_id, approver_principal_id="human-a")
    consumed = service.consume(request.approval_id, mandate_id="mandate-a")

    assert approved.status == ApprovalStatus.APPROVED
    assert approved.decision == ApprovalDecision.APPROVED
    assert approved.decided_by_principal_id == "human-a"
    assert consumed.status == ApprovalStatus.CONSUMED
    with pytest.raises(ValueError, match="CONSUMED"):
        service.consume(request.approval_id, mandate_id="mandate-b")


def test_refund_approval_is_bound_to_one_payment():
    service = _service()
    request = service.request_refund(
        tenant_id="tenant-a",
        requester_principal_id="agent-a",
        payment_id="pay-1",
        currency="INR",
        amount_minor=500,
    )

    assert request.action == ApprovalAction.REFUND_PAYMENT
    assert request.payment_id == "pay-1"
    assert request.payer is None and request.payee is None


def test_expired_pending_approval_cannot_be_approved():
    repository = FakeApprovalRepository()
    expired = ApprovalRequest(
        approval_id="appr-expired",
        tenant_id="tenant-a",
        requester_principal_id="agent-a",
        action=ApprovalAction.CREATE_PAYMENT,
        payer="acct-A",
        payee="acct-B",
        currency="INR",
        amount_minor=100,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    repository.create(expired)
    service = ApprovalService(repository)

    with pytest.raises(ValueError, match="EXPIRED"):
        service.approve(expired.approval_id, approver_principal_id="human-a")
    assert service.get(expired.approval_id).status == ApprovalStatus.EXPIRED


def test_action_binding_rejects_a_refund_without_payment_id():
    with pytest.raises(ValueError, match="refund approval requires payment_id"):
        ApprovalRequest(
            approval_id="appr-invalid",
            tenant_id="tenant-a",
            requester_principal_id="agent-a",
            action=ApprovalAction.REFUND_PAYMENT,
            currency="INR",
            amount_minor=100,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
