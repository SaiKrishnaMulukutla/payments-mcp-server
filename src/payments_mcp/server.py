"""FastMCP entrypoint: tools/resources/prompts, optional OAuth2.1 auth.

Tools are thin: resolve the per-request principal, then delegate to the PaymentGateway
(``operations.py``) and MandateAuthority (``mandate.py``). Over stdio (dev) the principal is the
demo principal; over authenticated HTTP it comes from the verified bearer token.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, cast

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field

from . import errors as E
from .backend import build_backend
from .config import AgentPrincipal, Settings, demo_principal
from .errors import BackendError
from .mandate import MandateAuthority, MandateVerifier
from .operations import Operations, PaymentGateway, build_operation_store


# ---------------------------------------------------------------------------
# Identity (folded from the former identity.py)
# ---------------------------------------------------------------------------


class TokenVerifier:
    """OAuth 2.1 resource-server identity: verify a bearer token, resolve the agent principal.

    HS256 for now; swap in JWKS/asymmetric by changing only TokenVerifier. The gateway validates
    tokens, it never issues them.
    """

    def __init__(
        self, secret: str, issuer: str | None = None, audience: str | None = None
    ) -> None:
        self._secret = secret
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> dict:
        import jwt

        try:
            return jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "sub"], "verify_aud": self._audience is not None},
            )
        except Exception as e:  # noqa: BLE001
            raise BackendError(E.UNAUTHENTICATED, "invalid or expired token") from e


class PrincipalStore:
    def __init__(self, principals: dict[str, AgentPrincipal]) -> None:
        self._by_sub = principals

    def get(self, sub: str) -> AgentPrincipal:
        principal = self._by_sub.get(sub)
        if principal is None:
            raise BackendError(E.UNAUTHENTICATED, f"unknown principal: {sub}")
        return principal


def resolve_principal(
    token: str, verifier: TokenVerifier, store: PrincipalStore
) -> AgentPrincipal:
    """Verify the token, load its principal, and narrow scopes to those the token also carries."""
    claims = verifier.verify(token)
    principal = store.get(str(claims["sub"]))
    token_scopes = str(claims.get("scope", "")).split()
    if token_scopes:
        effective = [s for s in principal.scopes if s in token_scopes]
        return principal.model_copy(update={"scopes": effective})
    return principal


class McpTokenVerifier:
    """Adapts our JWT verification to the MCP SDK's async TokenVerifier protocol."""

    def __init__(self, verifier: TokenVerifier, store: PrincipalStore) -> None:
        self._verifier = verifier
        self._store = store

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = self._verifier.verify(token)
            principal = self._store.get(str(claims["sub"]))
        except BackendError:
            return None
        token_scopes = str(claims.get("scope", "")).split()
        granted = (
            [s for s in principal.scopes if s in token_scopes]
            if token_scopes
            else list(principal.scopes)
        )
        return AccessToken(
            token=token,
            client_id=principal.principal_id,
            scopes=granted,
            expires_at=claims.get("exp"),
            subject=principal.principal_id,
            claims=dict(claims),
        )


def principal_from_access(access: AccessToken, store: PrincipalStore) -> AgentPrincipal:
    principal = store.get(str(access.subject or access.client_id))
    if access.scopes:
        effective = [s for s in principal.scopes if s in access.scopes]
        return principal.model_copy(update={"scopes": effective})
    return principal


settings = Settings()
_backend = build_backend(settings)
_ops = Operations(build_operation_store(settings.redis_url))
_mandates = (
    MandateVerifier(settings.mandate_secret, settings.mandate_issuer)
    if settings.mandate_secret
    else None
)
_authority = (
    MandateAuthority(settings.mandate_secret, settings.mandate_issuer)
    if settings.mandate_secret
    else None
)
_principal = demo_principal()
gateway = PaymentGateway(
    _backend,
    _principal,
    _ops,
    merchant_id=settings.merchant_id,
    mandate_verifier=_mandates,
    mandate_authority=_authority,
)

# ---- identity (folded from the former identity.py) ----
_principals = PrincipalStore({_principal.principal_id: _principal})
_verifier = (
    TokenVerifier(settings.auth_secret, settings.auth_issuer, settings.auth_audience)
    if settings.auth_secret
    else None
)

_auth_on = bool(_verifier and settings.auth_issuer and settings.auth_resource_url)
if _auth_on:
    assert _verifier and settings.auth_issuer and settings.auth_resource_url
    mcp = FastMCP(
        "payments",
        host=settings.host,
        port=settings.port,
        token_verifier=McpTokenVerifier(_verifier, _principals),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.auth_issuer),
            resource_server_url=AnyHttpUrl(settings.auth_resource_url),
        ),
    )
else:
    mcp = FastMCP("payments", host=settings.host, port=settings.port)


def _current_principal() -> AgentPrincipal:
    access = get_access_token()
    return principal_from_access(access, _principals) if access is not None else _principal


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
    return await gateway.get_payment(payment_id, principal=_current_principal())


@mcp.tool(annotations=_READ_ONLY)
async def get_balance(
    account_id: Annotated[str, Field(description="Account id the agent is allowed to view.")],
) -> dict:
    """Return the current balance (in minor units) of an allowed account."""
    return await gateway.get_balance(account_id, principal=_current_principal())


@mcp.tool(annotations=_READ_ONLY)
async def get_account_ledger(
    account_id: Annotated[str, Field(description="Account id whose postings to list.")],
    limit: Annotated[int, Field(gt=0, le=100, description="Max postings (newest first).")] = 20,
) -> dict:
    """Return a bounded page of an account's most-recent postings (newest first)."""
    return await gateway.get_account_ledger(account_id, limit, principal=_current_principal())


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
    mandate: Annotated[
        str | None,
        Field(description="Signed mandate authorizing this exact payment (payer/payee/amount)."),
    ] = None,
) -> dict:
    """Create a payment moving money from one account to another.

    Idempotent: retrying the same logical operation (same operation_id, same arguments) replays
    the original result instead of charging twice. Amounts over the agent's autonomous limit return
    an APPROVAL_REQUIRED response and are NOT executed.
    """
    return await gateway.create_payment(
        payer_account_id,
        payee_account_id,
        amount_minor,
        currency,
        operation_id,
        mandate,
        principal=_current_principal(),
    )


@mcp.tool(annotations=_DESTRUCTIVE)
async def refund_payment(
    payment_id: Annotated[str, Field(description="The payment to refund.")],
    amount_minor: Annotated[int, Field(gt=0, description="Refund amount in MINOR units.")],
    operation_id: Annotated[
        str | None, Field(description="Stable id; reuse when retrying so refunds happen at most once.")
    ] = None,
    mandate: Annotated[
        str | None, Field(description="Signed mandate authorizing this refund amount.")
    ] = None,
) -> dict:
    """Refund (part of) a payment. More consequential than a payment — it reverses money — so it
    needs the refund scope. Idempotent by operation_id; amounts over the agent's limit return
    APPROVAL_REQUIRED and are NOT executed.
    """
    return await gateway.refund_payment(
        payment_id, amount_minor, operation_id, mandate, principal=_current_principal()
    )


@mcp.tool(annotations=_READ_ONLY)
async def check_ledger_integrity() -> dict:
    """Admin-only: assert the ledger is balanced (net 0, no drifted accounts). Requires admin scope.

    Returns only the integrity summary — never raw account data.
    """
    return await gateway.ledger_integrity(principal=_current_principal())


@mcp.resource("payments://capabilities")
def capabilities() -> str:
    """What THIS agent principal may do — scopes, account allow-list, autonomous payment limit."""
    p = _current_principal()
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
    return json.dumps(await gateway.get_payment(payment_id, principal=_current_principal()))


@mcp.prompt()
def explain_payment(payment_id: str) -> str:
    """Prompt template: explain a payment's lifecycle using only tool-returned facts."""
    return (
        f"Use get_payment to fetch payment {payment_id}, then explain its lifecycle "
        "(hold -> authorize -> settle/reverse) in plain language. State only facts returned by "
        "the tools; do not invent any details the backend did not provide."
    )


def main() -> None:
    """Run the server. Transport from PAYMENTS_TRANSPORT: stdio (default) | streamable-http."""
    mcp.run(transport=cast(Literal["stdio", "sse", "streamable-http"], settings.transport))


if __name__ == "__main__":
    main()
