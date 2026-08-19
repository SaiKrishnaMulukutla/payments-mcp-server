"""A-2: MCP SDK token-verifier adapter + AccessToken -> principal mapping."""

import time

import jwt
from mcp.server.auth.provider import AccessToken

from payments_mcp.config import AgentPrincipal
from payments_mcp.server import (
    McpTokenVerifier,
    PrincipalStore,
    TokenVerifier,
    principal_from_access,
)

SECRET = "unit-test-signing-secret-0123456789ab"
ISS = "https://issuer.test"
AUD = "payments-mcp"


def _store() -> PrincipalStore:
    return PrincipalStore(
        {
            "svc-agent": AgentPrincipal(
                principal_id="svc-agent",
                allowed_accounts=["acct-A", "acct-B"],
                scopes=["payments:read", "payments:create", "payments:refund"],
                max_payment_amount_minor=10_000,
            )
        }
    )


def _tok(scope="payments:read payments:create", sub="svc-agent") -> str:
    return jwt.encode(
        {"sub": sub, "scope": scope, "iss": ISS, "aud": AUD, "exp": int(time.time()) + 60},
        SECRET,
        algorithm="HS256",
    )


def _adapter() -> McpTokenVerifier:
    return McpTokenVerifier(TokenVerifier(SECRET, ISS, AUD), _store())


async def test_valid_token_returns_access_token_with_narrowed_scopes():
    a = await _adapter().verify_token(_tok())
    assert a is not None and a.subject == "svc-agent"
    assert set(a.scopes) == {"payments:read", "payments:create"}


async def test_invalid_token_returns_none():
    assert await _adapter().verify_token(_tok()[:-3] + "xxx") is None


async def test_unknown_subject_returns_none():
    assert await _adapter().verify_token(_tok(sub="stranger")) is None


def test_principal_from_access_narrows_scopes():
    acc = AccessToken(token="t", client_id="svc-agent", scopes=["payments:read"], subject="svc-agent")
    p = principal_from_access(acc, _store())
    assert p.principal_id == "svc-agent" and p.scopes == ["payments:read"]
