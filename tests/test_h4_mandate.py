"""H4: signed payment mandate — verification + enforcement + idempotency anchoring."""

import time

import jwt
import pytest

from payments_mcp import errors as E
from payments_mcp.backend.base import BackendError
from payments_mcp.backend.demo_backend import DemoPaymentBackend
from payments_mcp.config import demo_principal
from payments_mcp.gateway import Gateway
from payments_mcp.mandate import MandateVerifier, sign_mandate

SECRET = "unit-test-mandate-secret-0123456789ab"


def _mandate(mandate_id="m-1", payer="acct-A", payee="acct-B", currency="INR", max_amount=5000) -> str:
    return sign_mandate(
        {
            "mandate_id": mandate_id,
            "payer": payer,
            "payee": payee,
            "currency": currency,
            "max_amount_minor": max_amount,
        },
        SECRET,
    )


def _gw() -> Gateway:
    return Gateway(
        DemoPaymentBackend(),
        demo_principal(),
        mandate_verifier=MandateVerifier(SECRET),
    )


def test_verify_roundtrip():
    m = MandateVerifier(SECRET).verify(_mandate())
    assert m.mandate_id == "m-1" and m.max_amount_minor == 5000


def test_expired_mandate_rejected():
    tok = sign_mandate(
        {"mandate_id": "m", "payer": "a", "payee": "b", "currency": "INR", "max_amount_minor": 1},
        SECRET,
        ttl_seconds=-1,
    )
    with pytest.raises(BackendError) as ei:
        MandateVerifier(SECRET).verify(tok)
    assert ei.value.code == E.MANDATE_INVALID


def test_forged_mandate_rejected():
    tok = sign_mandate(
        {"mandate_id": "m", "payer": "a", "payee": "b", "currency": "INR", "max_amount_minor": 1},
        "a-different-wrong-secret-0123456789ab",
    )
    with pytest.raises(BackendError) as ei:
        MandateVerifier(SECRET).verify(tok)
    assert ei.value.code == E.MANDATE_INVALID


async def test_create_with_valid_mandate_succeeds():
    g = _gw()
    r = await g.create_payment("acct-A", "acct-B", 500, mandate=_mandate())
    assert r["status"] == "SUCCEEDED"
    assert r["operation_id"] == "m-1"  # idempotency anchored to the mandate id


async def test_amount_over_mandate_rejected():
    g = _gw()
    r = await g.create_payment("acct-A", "acct-B", 9999, mandate=_mandate(max_amount=5000))
    assert r["code"] == E.MANDATE_MISMATCH
    assert (await g.get_balance("acct-A"))["balance_minor"] == 1_000_000  # not executed


async def test_wrong_account_rejected():
    g = _gw()
    r = await g.create_payment("acct-A", "acct-A", 100, mandate=_mandate(payee="acct-B"))
    assert r["code"] == E.MANDATE_MISMATCH


async def test_same_mandate_retry_replays_once():
    g = _gw()
    a = await g.create_payment("acct-A", "acct-B", 500, mandate=_mandate())
    b = await g.create_payment("acct-A", "acct-B", 500, mandate=_mandate())
    assert a["id"] == b["id"]
    assert (await g.get_balance("acct-A"))["balance_minor"] == 1_000_000 - 500


async def test_mandate_without_verifier_configured_is_rejected():
    g = Gateway(DemoPaymentBackend(), demo_principal())  # no mandate verifier
    r = await g.create_payment("acct-A", "acct-B", 500, mandate=_mandate())
    assert r["code"] == E.MANDATE_INVALID
