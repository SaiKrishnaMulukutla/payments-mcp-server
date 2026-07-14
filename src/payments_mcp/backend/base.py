"""PaymentBackend protocol + DTOs + BackendError.

Tools depend only on this protocol, never on raw httpx — so the same tools run against the
real Spring Boot service (HttpPaymentBackend) or an in-memory DemoPaymentBackend (dev + evals).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class Payment(BaseModel):
    id: str
    status: str
    amount_minor: int
    currency: str
    created_at: str


class Refund(BaseModel):
    id: str
    payment_id: str
    amount_minor: int
    status: str
    created_at: str


class Balance(BaseModel):
    account_id: str
    balance_minor: int
    currency: str
    updated_at: str


class LedgerEntry(BaseModel):
    transaction_id: str
    direction: str  # DEBIT | CREDIT
    amount_minor: int
    created_at: str


class LedgerPage(BaseModel):
    account_id: str
    count: int
    entries: list[LedgerEntry]


class IntegrityReport(BaseModel):
    balanced: bool
    ledger_net: int
    drifted_accounts: int


class BackendError(Exception):
    """A failure from the payments backend, tagged with a gateway taxonomy code (see errors.py)."""

    def __init__(
        self, code: str, message: str, *, retryable: bool = False, http_status: int | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status


@runtime_checkable
class PaymentBackend(Protocol):
    async def create_payment(
        self, *, idempotency_key: str, payer: str, payee: str, amount_minor: int, currency: str
    ) -> Payment: ...

    async def get_payment(self, payment_id: str) -> Payment: ...

    async def refund_payment(
        self, *, payment_id: str, amount_minor: int, idempotency_key: str
    ) -> Refund: ...

    async def get_balance(self, account_id: str) -> Balance: ...

    async def get_account_ledger(self, account_id: str, limit: int) -> LedgerPage: ...

    async def ledger_integrity(self) -> IntegrityReport: ...

    async def aclose(self) -> None: ...
