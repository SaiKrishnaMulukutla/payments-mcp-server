"""Gateway — the trust boundary. Holds a backend + principal + operation registry.

Every method is thin orchestration: validate -> authorize -> resolve operation identity ->
execute -> normalize -> audit. Money-correctness lives in the ledger; this layer governs what
the agent may request. Kept separate from server.py so it's unit-testable without stdio.
"""

from __future__ import annotations

import time

from . import audit, policy
from .backend.base import PaymentBackend
from .config import AgentPrincipal
from .errors import new_correlation_id, to_gateway_error
from .operations import Operations, args_hash


class Gateway:
    def __init__(
        self,
        backend: PaymentBackend,
        principal: AgentPrincipal,
        ops: Operations | None = None,
        merchant_id: str = "mcp-agent",
    ) -> None:
        self.backend = backend
        self.principal = principal
        self.ops = ops or Operations()
        self.merchant_id = merchant_id

    # ---- reads ----
    async def get_payment(self, payment_id: str) -> dict:
        cid = new_correlation_id()
        try:
            policy.require_scope(self.principal, policy.READ)
            return (await self.backend.get_payment(payment_id)).model_dump()
        except Exception as e:  # noqa: BLE001
            return to_gateway_error(e, cid).to_dict()

    async def get_balance(self, account_id: str) -> dict:
        cid = new_correlation_id()
        try:
            policy.require_scope(self.principal, policy.READ)
            policy.require_account(self.principal, account_id)
            return (await self.backend.get_balance(account_id)).model_dump()
        except Exception as e:  # noqa: BLE001
            return to_gateway_error(e, cid).to_dict()

    async def get_account_ledger(self, account_id: str, limit: int = 20) -> dict:
        cid = new_correlation_id()
        try:
            policy.require_scope(self.principal, policy.READ)
            policy.require_account(self.principal, account_id)
            bounded = max(1, min(limit, 100))
            return (await self.backend.get_account_ledger(account_id, bounded)).model_dump()
        except Exception as e:  # noqa: BLE001
            return to_gateway_error(e, cid).to_dict()

    # ---- consequential ----
    async def create_payment(
        self,
        payer_account_id: str,
        payee_account_id: str,
        amount_minor: int,
        currency: str = "INR",
        operation_id: str | None = None,
    ) -> dict:
        cid = new_correlation_id()
        t0 = time.perf_counter()
        args = {
            "payer": payer_account_id,
            "payee": payee_account_id,
            "amount_minor": amount_minor,
            "currency": currency,
        }
        op: str | None = None
        decision = "allowed"
        result_code = "ok"
        resource_id: str | None = None
        try:
            policy.validate_amount(amount_minor)
            policy.validate_currency(currency)
            policy.require_scope(self.principal, policy.CREATE)
            policy.require_account(self.principal, payer_account_id)
            policy.require_account(self.principal, payee_account_id)
            policy.require_within_limit(self.principal, amount_minor)
            idem, op = self.ops.resolve(
                self.principal.principal_id, "create_payment", args, operation_id
            )
            payment = await self.backend.create_payment(
                idempotency_key=idem,
                payer=payer_account_id,
                payee=payee_account_id,
                amount_minor=amount_minor,
                currency=currency,
            )
            resource_id = payment.id
            return payment.model_dump() | {"operation_id": op}
        except Exception as e:  # noqa: BLE001
            ge = to_gateway_error(e, cid)
            decision = result_code = ge.code
            out = ge.to_dict()
            if op:
                out["operation_id"] = op
            return out
        finally:
            audit.record(
                correlation_id=cid,
                principal_id=self.principal.principal_id,
                tool="create_payment",
                operation_id=op,
                args_hash=args_hash(self.principal.principal_id, "create_payment", args),
                policy_decision=decision,
                result_code=result_code,
                backend_resource_id=resource_id,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

    async def refund_payment(
        self, payment_id: str, amount_minor: int, operation_id: str | None = None
    ) -> dict:
        """More consequential than create — reverses a financial outcome. Needs the refund scope."""
        cid = new_correlation_id()
        t0 = time.perf_counter()
        args = {"payment_id": payment_id, "amount_minor": amount_minor}
        op: str | None = None
        decision = "allowed"
        result_code = "ok"
        resource_id: str | None = None
        try:
            policy.validate_amount(amount_minor)
            policy.require_scope(self.principal, policy.REFUND)
            policy.require_within_limit(self.principal, amount_minor)
            idem, op = self.ops.resolve(
                self.principal.principal_id, "refund_payment", args, operation_id
            )
            refund = await self.backend.refund_payment(
                payment_id=payment_id, amount_minor=amount_minor, idempotency_key=idem
            )
            resource_id = refund.id
            return refund.model_dump() | {"operation_id": op}
        except Exception as e:  # noqa: BLE001
            ge = to_gateway_error(e, cid)
            decision = result_code = ge.code
            out = ge.to_dict()
            if op:
                out["operation_id"] = op
            return out
        finally:
            audit.record(
                correlation_id=cid,
                principal_id=self.principal.principal_id,
                tool="refund_payment",
                operation_id=op,
                args_hash=args_hash(self.principal.principal_id, "refund_payment", args),
                policy_decision=decision,
                result_code=result_code,
                backend_resource_id=resource_id,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

    async def ledger_integrity(self) -> dict:
        """Admin-only integrity summary. Read-only; no raw account data."""
        cid = new_correlation_id()
        try:
            policy.require_scope(self.principal, policy.ADMIN)
            return (await self.backend.ledger_integrity()).model_dump()
        except Exception as e:  # noqa: BLE001
            return to_gateway_error(e, cid).to_dict()
