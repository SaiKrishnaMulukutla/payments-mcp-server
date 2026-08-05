"""H4 payment mandate: a signed, deterministic authorization for one specific transaction.

Minted by trusted code outside the model (an approval/checkout service); the gateway only verifies
and enforces it. HS256 now; asymmetric / Verifiable-Credential is a drop-in on the verifier.
"""

from __future__ import annotations

import time

from pydantic import BaseModel

from . import errors as E
from .backend.base import BackendError


class Mandate(BaseModel):
    mandate_id: str
    payer: str
    payee: str
    currency: str
    max_amount_minor: int


class MandateVerifier:
    def __init__(self, secret: str, issuer: str | None = None) -> None:
        self._secret = secret
        self._issuer = issuer

    def verify(self, token: str) -> Mandate:
        import jwt

        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                options={"require": ["exp", "mandate_id"], "verify_aud": False},
            )
        except Exception as e:  # noqa: BLE001
            raise BackendError(E.MANDATE_INVALID, "mandate missing, invalid, or expired") from e
        return Mandate(
            mandate_id=str(claims["mandate_id"]),
            payer=str(claims["payer"]),
            payee=str(claims["payee"]),
            currency=str(claims["currency"]),
            max_amount_minor=int(claims["max_amount_minor"]),
        )

    def enforce(
        self, m: Mandate, *, payer: str, payee: str, currency: str, amount_minor: int
    ) -> None:
        if payer != m.payer or payee != m.payee or currency != m.currency:
            raise BackendError(E.MANDATE_MISMATCH, "payment parties do not match the mandate")
        if amount_minor > m.max_amount_minor:
            raise BackendError(E.MANDATE_MISMATCH, "amount exceeds the mandate's authorized maximum")


def sign_mandate(
    claims: dict, secret: str, ttl_seconds: int = 900, issuer: str | None = None
) -> str:
    """Issuer/dev helper: mint a signed mandate. Production issuers hold the signing key."""
    import jwt

    payload = {**claims, "exp": int(time.time()) + ttl_seconds}
    if issuer:
        payload["iss"] = issuer
    return jwt.encode(payload, secret, algorithm="HS256")
