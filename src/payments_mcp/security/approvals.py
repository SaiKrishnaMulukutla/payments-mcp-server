"""Exact, tamper-evident approval binding.

The owner UI authenticates the owner separately. This signature only binds the approval
to immutable payment facts; PostgreSQL must still enforce expiry and single-use atomically.
"""

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

    def _canonical_payload(
        self,
        *,
        intent_id: str,
        account_id: str,
        snapshot_hash: str,
        amount_paise: int,
        fee_paise: int,
        expires_at: datetime,
    ) -> bytes:
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
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def sign(
        self,
        *,
        intent_id: str,
        account_id: str,
        snapshot_hash: str,
        amount_paise: int,
        fee_paise: int,
        expires_at: datetime,
    ) -> str:
        canonical = self._canonical_payload(
            intent_id=intent_id,
            account_id=account_id,
            snapshot_hash=snapshot_hash,
            amount_paise=amount_paise,
            fee_paise=fee_paise,
            expires_at=expires_at,
        )
        digest = hashlib.sha256(canonical).hexdigest()
        signature = hmac.new(self._secret, canonical, hashlib.sha256).hexdigest()
        return f"{digest}.{signature}"

    def verify(
        self,
        token: str,
        *,
        intent_id: str,
        account_id: str,
        snapshot_hash: str,
        amount_paise: int,
        fee_paise: int,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or datetime.now(timezone.utc)
        if expires_at.tzinfo is None or current_time.tzinfo is None:
            return False
        if expires_at <= current_time:
            return False
        expected = self.sign(
            intent_id=intent_id,
            account_id=account_id,
            snapshot_hash=snapshot_hash,
            amount_paise=amount_paise,
            fee_paise=fee_paise,
            expires_at=expires_at,
        )
        return hmac.compare_digest(token, expected)
