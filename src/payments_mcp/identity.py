"""OAuth 2.1 resource-server identity: verify a bearer token, resolve the agent principal.

HS256 for now; swap in JWKS/asymmetric by changing only TokenVerifier. The gateway validates
tokens, it never issues them.
"""

from __future__ import annotations

from mcp.server.auth.provider import AccessToken

from . import errors as E
from .backend.base import BackendError
from .config import AgentPrincipal


class TokenVerifier:
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
