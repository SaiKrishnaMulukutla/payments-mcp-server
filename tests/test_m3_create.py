"""M3 tests: safe agent payment — the core milestone.

Proves the gateway guarantees: idempotent replay, operation conflict, the retry-after-lost-response
case (exactly one financial side effect), authorization, approval ceiling, and funds checks.
"""

from payments_mcp.backend.demo_backend import DemoPaymentBackend
from payments_mcp.config import AgentPrincipal, demo_principal
from payments_mcp.gateway import Gateway
from payments_mcp.operations import Operations


def _gw(principal=None) -> Gateway:
    return Gateway(DemoPaymentBackend(), principal or demo_principal(), Operations())


async def test_happy_path_creates_once():
    g = _gw()
    r = await g.create_payment("acct-A", "acct-B", 500, operation_id="op-1")
    assert r["status"] == "SUCCEEDED"
    assert r["operation_id"] == "op-1"
    assert (await g.get_balance("acct-A"))["balance_minor"] == 1_000_000 - 500


async def test_retry_after_lost_response_is_exactly_once():
    """Backend commits, response 'lost', agent retries the SAME operation -> one payment only."""
    g = _gw()
    first = await g.create_payment("acct-A", "acct-B", 500, operation_id="op-X")
    retry = await g.create_payment("acct-A", "acct-B", 500, operation_id="op-X")  # lost-response retry
    assert first["id"] == retry["id"]  # replayed, not a second payment
    assert (await g.get_balance("acct-A"))["balance_minor"] == 1_000_000 - 500  # debited once


async def test_same_operation_different_payload_conflicts():
    g = _gw()
    await g.create_payment("acct-A", "acct-B", 500, operation_id="op-Y")
    dup = await g.create_payment("acct-A", "acct-B", 999, operation_id="op-Y")
    assert dup["code"] == "IDEMPOTENCY_CONFLICT"


async def test_derived_operation_id_dedupes_identical_calls():
    """No operation_id supplied: identical args derive the same id -> still exactly once."""
    g = _gw()
    a = await g.create_payment("acct-A", "acct-B", 250)
    b = await g.create_payment("acct-A", "acct-B", 250)
    assert a["id"] == b["id"]
    assert (await g.get_balance("acct-A"))["balance_minor"] == 1_000_000 - 250


async def test_unauthorized_account_makes_no_mutation():
    g = _gw()
    r = await g.create_payment("acct-A", "acct-Z", 100)  # acct-Z not in allow-list
    assert r["code"] == "ACCOUNT_NOT_ALLOWED"
    assert (await g.get_balance("acct-A"))["balance_minor"] == 1_000_000  # untouched


async def test_amount_over_limit_requires_approval_not_executed():
    g = _gw()
    r = await g.create_payment("acct-A", "acct-B", 50_000)  # > 10,000 ceiling
    assert r["code"] == "APPROVAL_REQUIRED"
    assert (await g.get_balance("acct-A"))["balance_minor"] == 1_000_000  # not executed


async def test_insufficient_funds_is_non_retryable():
    g = _gw()
    r = await g.create_payment("acct-B", "acct-A", 500)  # acct-B has 0
    assert r["code"] == "INSUFFICIENT_FUNDS"
    assert r["retryable"] is False


async def test_missing_create_scope_denied():
    ro = AgentPrincipal(scopes=["payments:read"], allowed_accounts=["acct-A", "acct-B"])
    g = _gw(ro)
    r = await g.create_payment("acct-A", "acct-B", 100)
    assert r["code"] == "PERMISSION_DENIED"
