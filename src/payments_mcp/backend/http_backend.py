"""HttpPaymentBackend — talks to the real Spring Boot payments service.

Maps to the verified endpoints and normalizes HTTP/ProblemDetail failures into BackendError
with a gateway taxonomy code.
"""

from __future__ import annotations

import httpx

from .. import errors as E
from ..config import Settings
from .base import (
    Balance,
    BackendError,
    IntegrityReport,
    LedgerEntry,
    LedgerPage,
    Payment,
    Refund,
)


class HttpPaymentBackend:
    def __init__(self, settings: Settings) -> None:
        self._merchant = settings.merchant_id
        headers = {"Authorization": f"Bearer {settings.token}"} if settings.token else {}
        self._c = httpx.AsyncClient(
            base_url=settings.base_url, headers=headers, timeout=settings.request_timeout_s
        )

    async def aclose(self) -> None:
        await self._c.aclose()

    # ---- transport helpers ----
    async def _send(self, method: str, path: str, *, not_found: str, **kw) -> dict:
        try:
            r = await self._c.request(method, path, **kw)
        except httpx.TimeoutException as e:
            raise BackendError(E.BACKEND_TIMEOUT, "payments backend timed out", retryable=True) from e
        except httpx.HTTPError as e:
            raise BackendError(
                E.BACKEND_UNAVAILABLE, "payments backend unreachable", retryable=True
            ) from e
        self._raise_for_status(r, not_found)
        return r.json() if r.content else {}

    @staticmethod
    def _raise_for_status(r: httpx.Response, not_found: str) -> None:
        if r.is_success:
            return
        detail = ""
        try:
            detail = str(r.json().get("detail", ""))
        except Exception:  # noqa: BLE001 - non-JSON body
            detail = r.text or ""
        s = r.status_code
        if s == 404:
            raise BackendError(not_found, detail or "not found", http_status=s)
        if s == 409:
            raise BackendError(E.IDEMPOTENCY_CONFLICT, detail or "idempotency conflict", http_status=s)
        if s == 422:
            if "fund" in detail.lower():
                raise BackendError(E.INSUFFICIENT_FUNDS, detail, http_status=s)
            raise BackendError(E.INVALID_ARGUMENT, detail or "unprocessable", http_status=s)
        if s == 429:
            raise BackendError(E.RATE_LIMITED, "backend rate limited", retryable=True, http_status=s)
        if s >= 500:
            raise BackendError(E.BACKEND_UNAVAILABLE, "backend error", retryable=True, http_status=s)
        raise BackendError(E.INTERNAL_ERROR, detail or f"unexpected status {s}", http_status=s)

    # ---- operations ----
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
        return _payment(await self._send("GET", f"/v1/payments/{payment_id}", not_found=E.PAYMENT_NOT_FOUND))

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
                transaction_id=str(e["transactionId"]),
                direction=str(e["direction"]),
                amount_minor=int(e["amount"]),
                created_at=str(e["createdAt"]),
            )
            for e in j.get("entries", [])
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
