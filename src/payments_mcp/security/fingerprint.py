"""Keyed account fingerprinting for allowlist lookup without storing raw identifiers."""

import hashlib
import hmac


class AccountFingerprinter:
    def __init__(self, secret: str) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("Account fingerprint secret must contain at least 32 bytes")
        self._secret = secret.encode("utf-8")

    def fingerprint(self, *, biller_id: str, normalized_customer_params: bytes) -> bytes:
        message = biller_id.encode("utf-8") + b"\x00" + normalized_customer_params
        return hmac.new(self._secret, message, hashlib.sha256).digest()
