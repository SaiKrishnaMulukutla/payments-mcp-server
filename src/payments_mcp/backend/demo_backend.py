"""DemoPaymentBackend — in-memory, correctness-preserving stand-in for dev + evals.

Enforces the guarantees the gateway cares about: idempotent replay (same key -> same payment),
non-negative balances (insufficient funds -> error), and balanced double-entry postings — so the
full agentic flow (incl. the retry eval) works with no local stack running.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .. import errors as E
from .base import (
    Balance,
    BackendError,
    IntegrityReport,
    LedgerEntry,
    LedgerPage,
    Payment,
    Refund,
)


class DemoPaymentBackend:
    def __init__(self) -> None:
        self._accounts: dict[str, dict] = {}
        self._payments: dict[str, Payment] = {}
        self._pay_by_key: dict[str, Payment] = {}
        self._refund_by_key: dict[str, Refund] = {}
        self._ledger: dict[str, list[LedgerEntry]] = defaultdict(list)
        self._seq = 0
        # seed two demo accounts
        self.seed("acct-A", 1_000_000, "INR")
        self.seed("acct-B", 0, "INR")

    def seed(self, account_id: str, balance_minor: int, currency: str = "INR") -> None:
        self._accounts[account_id] = {"balance": balance_minor, "currency": currency}

    async def aclose(self) -> None:  # noqa: D401 - protocol parity
        return None

    def _now(self) -> str:
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
        self._ledger[payer].append(
            LedgerEntry(transaction_id=txn, direction="DEBIT", amount_minor=amount_minor, created_at=now)
        )
        self._ledger[payee].append(
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
        entries = list(reversed(self._ledger[account_id]))[:limit]
        return LedgerPage(account_id=account_id, count=len(entries), entries=entries)

    async def ledger_integrity(self) -> IntegrityReport:
        net = 0
        for entries in self._ledger.values():
            for e in entries:
                net += e.amount_minor if e.direction == "DEBIT" else -e.amount_minor
        return IntegrityReport(balanced=net == 0, ledger_net=net, drifted_accounts=0)
