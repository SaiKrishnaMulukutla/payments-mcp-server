"""M1 tests: the backend abstraction + error normalization (via DemoPaymentBackend)."""

import pytest

from payments_mcp import errors as E
from payments_mcp.backend.base import BackendError
from payments_mcp.backend.demo_backend import DemoPaymentBackend
from payments_mcp.errors import new_correlation_id, to_gateway_error


async def test_create_and_get():
    b = DemoPaymentBackend()
    p = await b.create_payment(
        idempotency_key="k1", payer="acct-A", payee="acct-B", amount_minor=500, currency="INR"
    )
    assert p.status == "SUCCEEDED"
    assert (await b.get_payment(p.id)).id == p.id


async def test_idempotent_replay_debits_once():
    b = DemoPaymentBackend()
    p1 = await b.create_payment(
        idempotency_key="k1", payer="acct-A", payee="acct-B", amount_minor=500, currency="INR"
    )
    p2 = await b.create_payment(
        idempotency_key="k1", payer="acct-A", payee="acct-B", amount_minor=500, currency="INR"
    )
    assert p1.id == p2.id  # replay, not a second payment
    assert (await b.get_balance("acct-A")).balance_minor == 1_000_000 - 500


async def test_insufficient_funds_normalizes():
    b = DemoPaymentBackend()
    with pytest.raises(BackendError) as ei:
        await b.create_payment(
            idempotency_key="k2", payer="acct-B", payee="acct-A", amount_minor=999, currency="INR"
        )
    assert ei.value.code == E.INSUFFICIENT_FUNDS
    ge = to_gateway_error(ei.value, new_correlation_id())
    assert ge.code == E.INSUFFICIENT_FUNDS
    assert ge.retryable is False
    assert ge.suggested_action  # gateway-owned, present


async def test_ledger_balanced_after_payment():
    b = DemoPaymentBackend()
    await b.create_payment(
        idempotency_key="k1", payer="acct-A", payee="acct-B", amount_minor=500, currency="INR"
    )
    rep = await b.ledger_integrity()
    assert rep.balanced and rep.ledger_net == 0


async def test_payment_not_found():
    b = DemoPaymentBackend()
    with pytest.raises(BackendError) as ei:
        await b.get_payment("nope")
    assert ei.value.code == E.PAYMENT_NOT_FOUND


async def test_account_ledger_bounded():
    b = DemoPaymentBackend()
    for i in range(5):
        await b.create_payment(
            idempotency_key=f"k{i}", payer="acct-A", payee="acct-B", amount_minor=10, currency="INR"
        )
    page = await b.get_account_ledger("acct-A", limit=3)
    assert page.count == 3 and len(page.entries) == 3
