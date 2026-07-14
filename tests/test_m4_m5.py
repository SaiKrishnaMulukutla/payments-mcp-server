"""M4/M5 tests: refunds (risk tiering) + admin integrity boundary."""

from payments_mcp.backend.demo_backend import DemoPaymentBackend
from payments_mcp.config import AgentPrincipal, demo_principal
from payments_mcp.gateway import Gateway
from payments_mcp.operations import Operations


def _gw(principal) -> Gateway:
    return Gateway(DemoPaymentBackend(), principal, Operations())


def _refunder() -> AgentPrincipal:
    return AgentPrincipal(
        principal_id="refunder",
        allowed_accounts=["acct-A", "acct-B"],
        scopes=["payments:read", "payments:create", "payments:refund"],
        max_payment_amount_minor=10_000,
    )


async def test_refund_requires_refund_scope():
    g = _gw(demo_principal())  # read + create only
    r = await g.refund_payment("pay_000001", 100)
    assert r["code"] == "PERMISSION_DENIED"


async def test_refund_happy_and_idempotent_replay():
    g = _gw(_refunder())
    p = await g.create_payment("acct-A", "acct-B", 500, operation_id="op-p")
    r1 = await g.refund_payment(p["id"], 200, operation_id="op-r")
    assert r1["status"] == "SUCCEEDED"
    r2 = await g.refund_payment(p["id"], 200, operation_id="op-r")  # retry
    assert r1["id"] == r2["id"]


async def test_refund_over_limit_requires_approval():
    g = _gw(_refunder())
    r = await g.refund_payment("pay_anything", 50_000)  # > ceiling; rejected pre-backend
    assert r["code"] == "APPROVAL_REQUIRED"


async def test_integrity_requires_admin_scope():
    g = _gw(demo_principal())
    r = await g.ledger_integrity()
    assert r["code"] == "PERMISSION_DENIED"


async def test_integrity_admin_reports_balanced():
    admin = AgentPrincipal(
        principal_id="admin", scopes=["payments:admin"], allowed_accounts=["acct-A", "acct-B"]
    )
    g = _gw(admin)
    r = await g.ledger_integrity()
    assert r["balanced"] is True and r["ledger_net"] == 0
