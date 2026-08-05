"""OAuth 2.1 resource-server identity: verify a bearer token, resolve the agent principal.

HS256 for now; swap in JWKS/asymmetric by changing only TokenVerifier. The gateway validates
tokens, it never issues them.
"""

from __future__ import annotations

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
