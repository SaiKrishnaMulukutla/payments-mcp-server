"""FastMCP server entrypoint.

Thin: each tool delegates to the Gateway (validate -> authorize -> operation identity ->
execute -> normalize -> audit). Rich parameter schemas via Annotated + Field so the agent sees
descriptions/constraints; tool annotations (read-only / idempotent) so clients can reason about risk.
"""

from __future__ import annotations

import json
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .backend.demo_backend import DemoPaymentBackend
from .backend.http_backend import HttpPaymentBackend
from .config import Settings, demo_principal
from .gateway import Gateway

settings = Settings()
_backend = HttpPaymentBackend(settings) if settings.backend == "http" else DemoPaymentBackend()
gateway = Gateway(_backend, demo_principal(), merchant_id=settings.merchant_id)

mcp = FastMCP("payments")

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_IDEMPOTENT = ToolAnnotations(idempotentHint=True, destructiveHint=False)
_DESTRUCTIVE = ToolAnnotations(destructiveHint=True, idempotentHint=True)


@mcp.tool()
def ping() -> str:
    """Health check. Returns 'pong' if the payments MCP gateway is alive."""
    return "pong"


@mcp.tool(annotations=_READ_ONLY)
async def get_payment(
    payment_id: Annotated[str, Field(description="The payment id to fetch.")],
) -> dict:
    """Fetch a single payment by id. Read-only. Returns the payment or a structured error."""
    return await gateway.get_payment(payment_id)


@mcp.tool(annotations=_READ_ONLY)
async def get_balance(
    account_id: Annotated[str, Field(description="Account id the agent is allowed to view.")],
) -> dict:
    """Return the current balance (in minor units) of an allowed account."""
    return await gateway.get_balance(account_id)


@mcp.tool(annotations=_READ_ONLY)
async def get_account_ledger(
    account_id: Annotated[str, Field(description="Account id whose postings to list.")],
    limit: Annotated[int, Field(gt=0, le=100, description="Max postings (newest first).")] = 20,
) -> dict:
    """Return a bounded page of an account's most-recent postings (newest first)."""
    return await gateway.get_account_ledger(account_id, limit)


@mcp.tool(annotations=_IDEMPOTENT)
async def create_payment(
    payer_account_id: Annotated[str, Field(description="Account the money moves FROM.")],
    payee_account_id: Annotated[str, Field(description="Account the money moves TO.")],
    amount_minor: Annotated[int, Field(gt=0, description="Amount in MINOR units (paise/cents).")],
    currency: Annotated[str, Field(description="ISO currency code (INR or USD).")] = "INR",
    operation_id: Annotated[
        str | None,
        Field(
            description="Stable id for this logical operation. REUSE the same value when retrying "
            "so the payment is created at most once."
        ),
    ] = None,
) -> dict:
    """Create a payment moving money from one account to another.

    Idempotent: retrying the same logical operation (same operation_id, same arguments) replays
    the original result instead of charging twice. Amounts over the agent's autonomous limit return
    an APPROVAL_REQUIRED response and are NOT executed.
    """
    return await gateway.create_payment(
        payer_account_id, payee_account_id, amount_minor, currency, operation_id
    )


@mcp.tool(annotations=_DESTRUCTIVE)
async def refund_payment(
    payment_id: Annotated[str, Field(description="The payment to refund.")],
    amount_minor: Annotated[int, Field(gt=0, description="Refund amount in MINOR units.")],
    operation_id: Annotated[
        str | None, Field(description="Stable id; reuse when retrying so refunds happen at most once.")
    ] = None,
) -> dict:
    """Refund (part of) a payment. More consequential than a payment — it reverses money — so it
    needs the refund scope. Idempotent by operation_id; amounts over the agent's limit return
    APPROVAL_REQUIRED and are NOT executed.
    """
    return await gateway.refund_payment(payment_id, amount_minor, operation_id)


@mcp.tool(annotations=_READ_ONLY)
async def check_ledger_integrity() -> dict:
    """Admin-only: assert the ledger is balanced (net 0, no drifted accounts). Requires admin scope.

    Returns only the integrity summary — never raw account data.
    """
    return await gateway.ledger_integrity()


@mcp.resource("payments://capabilities")
def capabilities() -> str:
    """What THIS agent principal may do — scopes, account allow-list, autonomous payment limit."""
    p = gateway.principal
    accounts = ", ".join(p.allowed_accounts) if p.allowed_accounts else "unrestricted (dev)"
    return (
        f"principal: {p.principal_id}\n"
        f"scopes: {', '.join(p.scopes)}\n"
        f"allowed_accounts: {accounts}\n"
        f"autonomous_payment_limit_minor: {p.max_payment_amount_minor}"
    )


@mcp.resource("payments://payment/{payment_id}")
async def payment_resource(payment_id: str) -> str:
    """Bounded payment context for a given id (as JSON)."""
    return json.dumps(await gateway.get_payment(payment_id))


@mcp.prompt()
def explain_payment(payment_id: str) -> str:
    """Prompt template: explain a payment's lifecycle using only tool-returned facts."""
    return (
        f"Use get_payment to fetch payment {payment_id}, then explain its lifecycle "
        "(hold -> authorize -> settle/reverse) in plain language. State only facts returned by "
        "the tools; do not invent any details the backend did not provide."
    )


def main() -> None:
    """Run the server over stdio (used by MCP clients: Inspector, Claude Desktop/Code)."""
    mcp.run()


if __name__ == "__main__":
    main()
