"""H3: bearer-token verification + per-request principal resolution."""

import time

import jwt
import pytest

from payments_mcp import errors as E
from payments_mcp.backend.base import BackendError
from payments_mcp.backend.demo_backend import DemoPaymentBackend
from payments_mcp.config import AgentPrincipal
from payments_mcp.gateway import Gateway
from payments_mcp.identity import PrincipalStore, TokenVerifier, resolve_principal

SECRET = "unit-test-signing-secret-0123456789ab"
ISS = "https://issuer.test"
AUD = "payments-mcp"


def _token(sub="svc-agent", scope="payments:read payments:create", **extra) -> str:
    claims = {"sub": sub, "scope": scope, "iss": ISS, "aud": AUD, "exp": int(time.time()) + 60}
    claims.update(extra)
    return jwt.encode(claims, SECRET, algorithm="HS256")


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


def _verifier() -> TokenVerifier:
    return TokenVerifier(SECRET, ISS, AUD)


def test_valid_token_resolves_principal():
    p = resolve_principal(_token(), _verifier(), _store())
    assert p.principal_id == "svc-agent"


def test_token_scopes_narrow_principal_scopes():
    p = resolve_principal(_token(scope="payments:read"), _verifier(), _store())
    assert p.scopes == ["payments:read"]  # refund/create dropped though the store grants them


def test_expired_token_rejected():
    tok = jwt.encode(
        {"sub": "svc-agent", "iss": ISS, "aud": AUD, "exp": int(time.time()) - 1},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(BackendError) as ei:
        _verifier().verify(tok)
    assert ei.value.code == E.UNAUTHENTICATED


def test_bad_signature_rejected():
    tok = _token()[:-4] + "aaaa"
    with pytest.raises(BackendError) as ei:
        _verifier().verify(tok)
    assert ei.value.code == E.UNAUTHENTICATED


def test_wrong_audience_rejected():
    tok = jwt.encode(
        {"sub": "svc-agent", "iss": ISS, "aud": "other", "exp": int(time.time()) + 60},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(BackendError) as ei:
        _verifier().verify(tok)
    assert ei.value.code == E.UNAUTHENTICATED


def test_unknown_subject_rejected():
    with pytest.raises(BackendError) as ei:
        resolve_principal(_token(sub="stranger"), _verifier(), _store())
    assert ei.value.code == E.UNAUTHENTICATED


async def test_resolved_principal_drives_gateway_authorization():
    g = Gateway(DemoPaymentBackend(), _store().get("svc-agent"))
    read_only = resolve_principal(_token(scope="payments:read"), _verifier(), _store())
    r = await g.create_payment("acct-A", "acct-B", 100, principal=read_only)
    assert r["code"] == E.PERMISSION_DENIED  # token lacked create scope
    assert (await g.get_balance("acct-A", principal=read_only))["balance_minor"] == 1_000_000
