"""Payment provider backend: protocol + Demo (mock) + HTTP implementations.

Tools depend only on the ``PaymentBackend`` protocol, never on raw httpx — so the same tools run
against the real Spring Boot service (``HttpPaymentBackend``) or an in-memory ``DemoPaymentBackend``
(dev + evals). The mock will be removed in a later increment.
"""

from __future__ import annotations

import httpx
from typing import Protocol, runtime_checkable

from . import errors as E
from .config import Settings
from .errors import BackendError
from .models import (
    Balance,
    IntegrityReport,
    LedgerEntry,
    LedgerPage,
    Payment,
    Refund,
)


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


class DemoPaymentBackend:
    """In-memory, correctness-preserving stand-in for dev + evals.

    Enforces the guarantees the gateway cares about: idempotent replay (same key -> same payment),
    non-negative balances (insufficient funds -> error), and balanced double-entry postings — so the
    full agentic flow (including the retry eval) works with no local stack running.
    """

    def __init__(self) -> None:
        self._accounts: dict[str, dict] = {}
        self._payments: dict[str, Payment] = {}
        self._pay_by_key: dict[str, Payment] = {}
        self._refund_by_key: dict[str, Refund] = {}
        self._ledger: dict[str, list[LedgerEntry]] = {}
        self._seq = 0
        # seed two demo accounts
        self.seed("acct-A", 1_000_000, "INR")
        self.seed("acct-B", 0, "INR")

    def seed(self, account_id: str, balance_minor: int, currency: str = "INR") -> None:
        self._accounts[account_id] = {"balance": balance_minor, "currency": currency}

    async def aclose(self) -> None:
        return None

    def _now(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq:06d}"

    def _require_account(self, account_id: str) -> dict:
        acct = self._accounts.get(account_id)
        if acct is None:
            raise BackendError(E.ACCOUNT_NOT_FOUND, f"account not found: {account_id}")
        return acct

    async def create_payment(
        self, *, idempotency_key: str, payer: str, payee: str, amount_minor: int, currency: str
    ) -> Payment:
        if idempotency_key in self._pay_by_key:
            return self._pay_by_key[idempotency_key]  # replay — no second side effect
        payer_acct = self._require_account(payer)
        self._require_account(payee)
        if payer_acct["balance"] < amount_minor:
            raise BackendError(E.INSUFFICIENT_FUNDS, "payer has insufficient balance")

        self._accounts[payer]["balance"] -= amount_minor
        self._accounts[payee]["balance"] += amount_minor
        now = self._now()
        payment = Payment(
            id=self._next("pay"),
            status="SUCCEEDED",
            amount_minor=amount_minor,
            currency=currency,
            created_at=now,
        )
        self._payments[payment.id] = payment
        self._pay_by_key[idempotency_key] = payment
        txn = self._next("txn")
        self._ledger.setdefault(payer, []).append(
            LedgerEntry(transaction_id=txn, direction="DEBIT", amount_minor=amount_minor, created_at=now)
        )
        self._ledger.setdefault(payee, []).append(
            LedgerEntry(transaction_id=txn, direction="CREDIT", amount_minor=amount_minor, created_at=now)
        )
        return payment

    async def get_payment(self, payment_id: str) -> Payment:
        payment = self._payments.get(payment_id)
        if payment is None:
            raise BackendError(E.PAYMENT_NOT_FOUND, f"payment not found: {payment_id}")
        return payment

    async def refund_payment(
        self, *, payment_id: str, amount_minor: int, idempotency_key: str
    ) -> Refund:
        if idempotency_key in self._refund_by_key:
            return self._refund_by_key[idempotency_key]  # replay
        await self.get_payment(payment_id)  # 404 if missing
        now = self._now()
        refund = Refund(
            id=self._next("ref"),
            payment_id=payment_id,
            amount_minor=amount_minor,
            status="SUCCEEDED",
            created_at=now,
        )
        self._refund_by_key[idempotency_key] = refund
        return refund

    async def get_balance(self, account_id: str) -> Balance:
        acct = self._require_account(account_id)
        return Balance(
            account_id=account_id,
            balance_minor=acct["balance"],
            currency=acct["currency"],
            updated_at=self._now(),
        )

    async def get_account_ledger(self, account_id: str, limit: int) -> LedgerPage:
        self._require_account(account_id)
        entries = list(reversed(self._ledger.get(account_id, [])))[:limit]
        return LedgerPage(account_id=account_id, count=len(entries), entries=entries)

    async def ledger_integrity(self) -> IntegrityReport:
        net = 0
        for entries in self._ledger.values():
            for entry in entries:
                net += entry.amount_minor if entry.direction == "DEBIT" else -entry.amount_minor
        return IntegrityReport(balanced=net == 0, ledger_net=net, drifted_accounts=0)


class HttpPaymentBackend:
    """Talks to the real Spring Boot payments service.

    Maps to the verified endpoints and normalizes HTTP/ProblemDetail failures into BackendError
    with a gateway taxonomy code.
    """

    def __init__(self, settings: Settings) -> None:
        import httpx

        self._htx = httpx
        self._merchant = settings.merchant_id
        headers = {"Authorization": f"Bearer {settings.token}"} if settings.token else {}
        self._c = httpx.AsyncClient(
            base_url=settings.base_url, headers=headers, timeout=settings.request_timeout_s
        )

    async def aclose(self) -> None:
        await self._c.aclose()

    async def _send(self, method: str, path: str, *, not_found: str, **kw) -> dict:
        try:
            r = await self._c.request(method, path, **kw)
        except self._htx.TimeoutException as e:
            raise BackendError(E.BACKEND_TIMEOUT, "payments backend timed out", retryable=True) from e
        except self._htx.HTTPError as e:
            raise BackendError(
                E.BACKEND_UNAVAILABLE, "payments backend unreachable", retryable=True
            ) from e
        self._raise_for_status(r, not_found)
        return r.json() if r.content else {}

    @staticmethod
    def _raise_for_status(r, not_found: str) -> None:
        if r.is_success:
            return
        detail = ""
        try:
            detail = str(r.json().get("detail", ""))
        except Exception:  # noqa: BLE001 - non-JSON body
            detail = r.text or ""
        d = detail.lower()
        status = r.status_code
        if status == 404:
            raise BackendError(not_found, detail or "not found", http_status=status)
        if status == 409:
            raise BackendError(
                E.OPERATION_IN_PROGRESS,
                detail or "operation already in progress",
                retryable=True,
                http_status=status,
            )
        if status == 422:
            if "insufficient funds" in d:
                raise BackendError(E.INSUFFICIENT_FUNDS, detail, http_status=status)
            if "different request body" in d or ("idempotency" in d and "different" in d):
                raise BackendError(E.IDEMPOTENCY_CONFLICT, detail, http_status=status)
            raise BackendError(E.INVALID_ARGUMENT, detail or "unprocessable", http_status=status)
        if status == 429:
            raise BackendError(E.RATE_LIMITED, "backend rate limited", retryable=True, http_status=status)
        if status >= 500:
            raise BackendError(E.BACKEND_UNAVAILABLE, "backend error", retryable=True, http_status=status)
        raise BackendError(E.INTERNAL_ERROR, detail or f"unexpected status {status}", http_status=status)

    async def create_payment(
        self, *, idempotency_key: str, payer: str, payee: str, amount_minor: int, currency: str
    ) -> Payment:
        j = await self._send(
            "POST",
            "/v1/payments",
            not_found=E.PAYMENT_NOT_FOUND,
            headers={"Idempotency-Key": idempotency_key},
            json={
                "merchantId": self._merchant,
                "payerAccountId": payer,
                "payeeAccountId": payee,
                "amount": amount_minor,
                "currency": currency,
            },
        )
        return _payment(j)

    async def get_payment(self, payment_id: str) -> Payment:
        return _payment(
            await self._send("GET", f"/v1/payments/{payment_id}", not_found=E.PAYMENT_NOT_FOUND)
        )

    async def refund_payment(
        self, *, payment_id: str, amount_minor: int, idempotency_key: str
    ) -> Refund:
        j = await self._send(
            "POST",
            f"/v1/payments/{payment_id}/refunds",
            not_found=E.PAYMENT_NOT_FOUND,
            headers={"Idempotency-Key": idempotency_key},
            json={"amount": amount_minor},
        )
        return Refund(
            id=str(j["id"]),
            payment_id=str(j["paymentId"]),
            amount_minor=int(j["amount"]),
            status=str(j["status"]),
            created_at=str(j["createdAt"]),
        )

    async def get_balance(self, account_id: str) -> Balance:
        j = await self._send(
            "GET", f"/v1/accounts/{account_id}/balance", not_found=E.ACCOUNT_NOT_FOUND
        )
        return Balance(
            account_id=str(j["accountId"]),
            balance_minor=int(j["balance"]),
            currency=str(j["currency"]),
            updated_at=str(j["updatedAt"]),
        )

    async def get_account_ledger(self, account_id: str, limit: int) -> LedgerPage:
        j = await self._send(
            "GET",
            f"/v1/accounts/{account_id}/ledger",
            not_found=E.ACCOUNT_NOT_FOUND,
            params={"limit": limit},
        )
        entries = [
            LedgerEntry(
                transaction_id=str(entry["transactionId"]),
                direction=str(entry["direction"]),
                amount_minor=int(entry["amount"]),
                created_at=str(entry["createdAt"]),
            )
            for entry in j.get("entries", [])
        ]
        return LedgerPage(account_id=str(j["accountId"]), count=int(j["count"]), entries=entries)

    async def ledger_integrity(self) -> IntegrityReport:
        j = await self._send("GET", "/v1/reconciliation/report", not_found=E.INTERNAL_ERROR)
        return IntegrityReport(
            balanced=bool(j["balanced"]),
            ledger_net=int(j["ledgerNet"]),
            drifted_accounts=int(j["driftedAccounts"]),
        )


def _payment(j: dict) -> Payment:
    return Payment(
        id=str(j["id"]),
        status=str(j["status"]),
        amount_minor=int(j["amount"]),
        currency=str(j["currency"]),
        created_at=str(j["createdAt"]),
    )


def build_backend(settings: Settings) -> PaymentBackend:
    """Construct the configured backend: ``demo`` (in-memory) or ``http`` (Spring Boot)."""
    if settings.backend == "http":
        return HttpPaymentBackend(settings)
    return DemoPaymentBackend()


__all__ = [
    "Balance",
    "DemoPaymentBackend",
    "HttpPaymentBackend",
    "IntegrityReport",
    "LedgerEntry",
    "LedgerPage",
    "Payment",
    "PaymentBackend",
    "Refund",
    "build_backend",
]
