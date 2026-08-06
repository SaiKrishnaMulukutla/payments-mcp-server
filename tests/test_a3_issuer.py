"""A-3: approval -> mandate issuer (human-in-the-loop closes into a signed mandate)."""

import pytest

from payments_mcp.issuer import APPROVED, PENDING, Issuer
from payments_mcp.mandate import MandateVerifier

SECRET = "issuer-signing-secret-0123456789abcd"


def _issuer() -> Issuer:
    return Issuer(SECRET)


def test_request_is_pending_and_listed():
    i = _issuer()
    r = i.request(payer="acct-A", payee="acct-B", currency="INR", amount_minor=5000)
    assert r.status == PENDING and r.approval_id.startswith("appr-")
    assert [x.approval_id for x in i.pending()] == [r.approval_id]


def test_approve_mints_a_valid_enforceable_mandate():
    i = _issuer()
    r = i.request(payer="acct-A", payee="acct-B", currency="INR", amount_minor=5000)
    token = i.approve(r.approval_id)
    m = MandateVerifier(SECRET).verify(token)
    assert m.mandate_id == r.approval_id and m.max_amount_minor == 5000
    MandateVerifier(SECRET).enforce(
        m, payer="acct-A", payee="acct-B", currency="INR", amount_minor=5000
    )
    assert i.get(r.approval_id).status == APPROVED
    assert i.pending() == []


def test_double_approve_rejected():
    i = _issuer()
    r = i.request(payer="a", payee="b", currency="INR", amount_minor=1)
    i.approve(r.approval_id)
    with pytest.raises(ValueError):
        i.approve(r.approval_id)


def test_reject_then_cannot_approve():
    i = _issuer()
    r = i.request(payer="a", payee="b", currency="INR", amount_minor=1)
    i.reject(r.approval_id)
    with pytest.raises(ValueError):
        i.approve(r.approval_id)


def test_unknown_approval_id():
    with pytest.raises(KeyError):
        _issuer().approve("nope")
