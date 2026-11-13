"""Approval-binding primitives; HTTP/UI authentication and DB single-use are separate controls."""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any


class ApprovalSigner:
    def __init__(self, secret: str) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("Approval signing secret must contain at least 32 bytes")
        self._secret = secret.encode("utf-8")

    def sign(self, *, intent_id: str, account_id: str, snapshot_hash: str, amount_paise: int, fee_paise: int, expires_at: datetime) -> str:
        if expires_at.tzinfo is None:
            raise ValueError("Approval expiry must be timezone-aware")
        payload: dict[str, Any] = {
            "intent_id": intent_id,
            "account_id": account_id,
            "snapshot_hash": snapshot_hash,
            "amount_paise": amount_paise,
            "fee_paise": fee_paise,
            "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._secret, canonical, hashlib.sha256).hexdigest()
        return f"{hashlib.sha256(canonical).hexdigest()}.{signature}"

    def verify(self, token: str, *, expected_binding: str) -> bool:
        return hmac.compare_digest(token, expected_binding)
