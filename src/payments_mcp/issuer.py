"""Approval -> mandate issuer: holds the signing key, mints a mandate only after human approval."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from .mandate import sign_mandate

PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"


class ApprovalRequest(BaseModel):
    approval_id: str
    payer: str
    payee: str
    currency: str
    amount_minor: int
    status: str = PENDING


class Issuer:
    def __init__(self, secret: str, issuer: str | None = None, ttl_seconds: int = 900) -> None:
        self._secret = secret
        self._issuer = issuer
        self._ttl = ttl_seconds
        self._requests: dict[str, ApprovalRequest] = {}

    def request(
        self, *, payer: str, payee: str, currency: str, amount_minor: int
    ) -> ApprovalRequest:
        rid = "appr-" + uuid.uuid4().hex[:12]
        req = ApprovalRequest(
            approval_id=rid, payer=payer, payee=payee, currency=currency, amount_minor=amount_minor
        )
        self._requests[rid] = req
        return req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._requests.get(approval_id)

    def pending(self) -> list[ApprovalRequest]:
        return [r for r in self._requests.values() if r.status == PENDING]

    def approve(self, approval_id: str) -> str:
        req = self._requests.get(approval_id)
        if req is None:
            raise KeyError(approval_id)
        if req.status != PENDING:
            raise ValueError(f"approval {approval_id} is already {req.status}")
        req.status = APPROVED
        return sign_mandate(
            {
                "mandate_id": req.approval_id,
                "payer": req.payer,
                "payee": req.payee,
                "currency": req.currency,
                "max_amount_minor": req.amount_minor,
            },
            self._secret,
            self._ttl,
            self._issuer,
        )

    def reject(self, approval_id: str) -> None:
        req = self._requests.get(approval_id)
        if req is None:
            raise KeyError(approval_id)
        req.status = REJECTED
