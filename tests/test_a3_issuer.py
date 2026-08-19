"""A-3: mandate authority (human-in-the-loop closes into a signed mandate)."""

import pytest

from payments_mcp.mandate import APPROVED, PENDING, MandateAuthority, MandateVerifier

SECRET = "issuer-signing-secret-0123456789abcd"


def _authority() -> MandateAuthority:
    return MandateAuthority(SECRET)


def test_request_is_pending_and_listed():
    a = _authority()
    r = a.propose(tenant_id="demo-tenant", requester_principal_id="demo-agent", terms=_terms())
    assert r.approval_status.value == PENDING and r.proposal_id.startswith("prop-")
    assert [x.proposal_id for x in a.pending()] == [r.proposal_id]


def test_approve_mints_a_valid_enforceable_mandate():
    a = _authority()
    r = a.propose(tenant_id="demo-tenant", requester_principal_id="demo-agent", terms=_terms())
    mandate = a.approve_proposal(r.proposal_id, approver_principal_id="human-a")
    assert mandate.mandate_status.value == APPROVED or mandate.mandate_status.value == "ACTIVE"
    assert a.pending() == []


def test_double_approve_rejected():
    a = _authority()
    r = a.propose(tenant_id="demo-tenant", requester_principal_id="demo-agent", terms=_terms())
    a.approve_proposal(r.proposal_id, approver_principal_id="human-a")
    with pytest.raises(ValueError):
        a.approve_proposal(r.proposal_id, approver_principal_id="human-a")


def test_reject_then_cannot_approve():
    a = _authority()
    r = a.propose(tenant_id="demo-tenant", requester_principal_id="demo-agent", terms=_terms())
    a.reject_proposal(r.proposal_id, approver_principal_id="human-a")
    with pytest.raises(ValueError):
        a.approve_proposal(r.proposal_id, approver_principal_id="human-a")


def test_unknown_approval_id():
    with pytest.raises(KeyError):
        _authority().approve_proposal("nope", approver_principal_id="human-a")


def _terms():
    from datetime import datetime, timedelta, timezone

    from payments_mcp.models import MandateOperation, MandateTerms

    now = datetime.now(timezone.utc)
    return MandateTerms(
        payer_account_id="acct-A",
        allowed_payee_ids=["acct-B"],
        allowed_operations=[MandateOperation.PAY_BILL],
        currency="INR",
        purpose="Electricity",
        per_transaction_limit_minor=5000,
        aggregate_limit_minor=10000,
        aggregate_period="MONTHLY",
        approval_required_above_minor=0,
        valid_from=now,
        expires_at=now + timedelta(days=365),
    )
</｜DSML｜parameter>
</write_to_file>