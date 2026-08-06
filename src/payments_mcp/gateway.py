"""Gateway — the trust boundary: validate -> authorize -> resolve identity -> execute -> audit.

Money-correctness lives in the ledger; this layer governs what an agent may request. The principal
is resolved per request (from a verified token in the HTTP path) with a constructor default for the
stdio/demo path.
"""

from __future__ import annotations

import time

from . import audit, policy
from . import errors as E
from .backend.base import BackendError, PaymentBackend
from .config import AgentPrincipal
from .errors import new_correlation_id, to_gateway_error
from .mandate import Mandate, MandateVerifier
from .operations import Operations, args_hash


class Gateway:
    def __init__(
        self,
        backend: PaymentBackend,
        principal: AgentPrincipal,
        ops: Operations | None = None,
        merchant_id: str = "mcp-agent",
        mandate_verifier: MandateVerifier | None = None,
    ) -> None:
        self.backend = backend
        self.principal = principal
        self.ops = ops or Operations()
        self.merchant_id = merchant_id
        self.mandates = mandate_verifier

    # ---- reads ----
    async def get_payment(self, payment_id: str, principal: AgentPrincipal | None = None) -> dict:
        cid = new_correlation_id()
        try:
            policy.require_scope(principal or self.principal, policy.READ)
            return (await self.backend.get_payment(payment_id)).model_dump()
        except Exception as e:  # noqa: BLE001
            return to_gateway_error(e, cid).to_dict()

    async def get_balance(self, account_id: str, principal: AgentPrincipal | None = None) -> dict:
        cid = new_correlation_id()
        try:
            p = principal or self.principal
            policy.require_scope(p, policy.READ)
            policy.require_account(p, account_id)
            return (await self.backend.get_balance(account_id)).model_dump()
        except Exception as e:  # noqa: BLE001
            return to_gateway_error(e, cid).to_dict()

    async def get_account_ledger(
        self, account_id: str, limit: int = 20, principal: AgentPrincipal | None = None
    ) -> dict:
        cid = new_correlation_id()
        try:
            p = principal or self.principal
            policy.require_scope(p, policy.READ)
            policy.require_account(p, account_id)
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
        mandate: str | None = None,
        principal: AgentPrincipal | None = None,
    ) -> dict:
        p = principal or self.principal
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
            policy.require_scope(p, policy.CREATE)
            policy.require_account(p, payer_account_id)
            policy.require_account(p, payee_account_id)
            policy.require_within_limit(p, amount_minor)
            if mandate is not None:
                operation_id = self._apply_mandate(
                    mandate,
                    payer=payer_account_id,
                    payee=payee_account_id,
                    currency=currency,
                    amount_minor=amount_minor,
                )
            idem, op = await self.ops.resolve(p.principal_id, "create_payment", args, operation_id)
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
            if op:
                await self.ops.complete(op)
            audit.record(
                correlation_id=cid,
                principal_id=p.principal_id,
                tool="create_payment",
                operation_id=op,
                args_hash=args_hash(p.principal_id, "create_payment", args),
                policy_decision=decision,
                result_code=result_code,
                backend_resource_id=resource_id,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

    async def refund_payment(
        self,
        payment_id: str,
        amount_minor: int,
        operation_id: str | None = None,
        mandate: str | None = None,
        principal: AgentPrincipal | None = None,
    ) -> dict:
        p = principal or self.principal
        cid = new_correlation_id()
        t0 = time.perf_counter()
        args = {"payment_id": payment_id, "amount_minor": amount_minor}
        op: str | None = None
        decision = "allowed"
        result_code = "ok"
        resource_id: str | None = None
        try:
            policy.validate_amount(amount_minor)
            policy.require_scope(p, policy.REFUND)
            policy.require_within_limit(p, amount_minor)
            if mandate is not None:
                m = self._verify_mandate(mandate)
                if amount_minor > m.max_amount_minor:
                    raise BackendError(
                        E.MANDATE_MISMATCH, "amount exceeds the mandate's authorized maximum"
                    )
                operation_id = m.mandate_id
            idem, op = await self.ops.resolve(p.principal_id, "refund_payment", args, operation_id)
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
            if op:
                await self.ops.complete(op)
            audit.record(
                correlation_id=cid,
                principal_id=p.principal_id,
                tool="refund_payment",
                operation_id=op,
                args_hash=args_hash(p.principal_id, "refund_payment", args),
                policy_decision=decision,
                result_code=result_code,
                backend_resource_id=resource_id,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

    async def ledger_integrity(self, principal: AgentPrincipal | None = None) -> dict:
        cid = new_correlation_id()
        try:
            policy.require_scope(principal or self.principal, policy.ADMIN)
            return (await self.backend.ledger_integrity()).model_dump()
        except Exception as e:  # noqa: BLE001
            return to_gateway_error(e, cid).to_dict()

    def _verify_mandate(self, mandate: str) -> Mandate:
        if self.mandates is None:
            raise BackendError(E.MANDATE_INVALID, "mandates are not enabled")
        return self.mandates.verify(mandate)

    def _apply_mandate(
        self, mandate: str, *, payer: str, payee: str, currency: str, amount_minor: int
    ) -> str:
        if self.mandates is None:
            raise BackendError(E.MANDATE_INVALID, "mandates are not enabled")
        m = self.mandates.verify(mandate)
        self.mandates.enforce(
            m, payer=payer, payee=payee, currency=currency, amount_minor=amount_minor
        )
        return m.mandate_id
