"""Payment-operation domain — the execution boundary.

Everything about exercising a payment operation lives here: idempotency, reserve, execute,
complete, fail, unknown, reconciliation, payment status, and the gateway orchestration that ties
policy checks, mandate enforcement, op-store coordination and audit into one flow.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from . import errors as E, policy
from .backend import PaymentBackend
from .config import AgentPrincipal
from .errors import BackendError, new_correlation_id, to_gateway_error
from .mandate import MandateAuthority, MandateVerifier
from .models import (
    Mandate,
    OperationState,
    PaymentOperation,
)

logger = logging.getLogger("payments_mcp.audit")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _record_audit(
    *,
    correlation_id: str,
    principal_id: str,
    tool: str,
    operation_id: str | None,
    args_hash: str,
    policy_decision: str,
    result_code: str,
    backend_resource_id: str | None = None,
    latency_ms: int,
) -> None:
    """Audit trail: correlate agent intent with backend execution.

    Logs to a logger (stderr) so it never corrupts the stdio MCP protocol on stdout. Raw arguments
    are never logged — only a hash — so sensitive values don't leak into logs.
    """
    logger.info(
        "AUDIT %s",
        json.dumps(
            {
                "correlation_id": correlation_id,
                "principal_id": principal_id,
                "tool": tool,
                "operation_id": operation_id,
                "args_hash": args_hash,
                "policy_decision": policy_decision,
                "backend_resource_id": backend_resource_id,
                "result_code": result_code,
                "latency_ms": latency_ms,
            }
        ),
    )


# ---------------------------------------------------------------------------
# Idempotency machinery (operation store + Operations adapter)
# ---------------------------------------------------------------------------

MAP_TTL_SECONDS = 24 * 60 * 60
LOCK_TTL_SECONDS = 30


@runtime_checkable
class OperationStore(Protocol):
    async def reserve(self, op: str, args_hash: str) -> None:
        """Reserve op; raise IDEMPOTENCY_CONFLICT on payload reuse, OPERATION_IN_PROGRESS if locked."""
        ...

    async def release(self, op: str) -> None:
        """Release the in-flight lock; the op->hash record persists for replay/conflict checks."""
        ...


class InMemoryOperationStore:
    """Process-local store; the default when no Redis is configured."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}
        self._locked: set[str] = set()

    async def reserve(self, op: str, args_hash: str) -> None:
        prev = self._seen.get(op)
        if prev is not None and prev != args_hash:
            raise BackendError(E.IDEMPOTENCY_CONFLICT, "operation_id reused with a different payload")
        if op in self._locked:
            raise BackendError(
                E.OPERATION_IN_PROGRESS, "operation already in progress", retryable=True
            )
        self._seen[op] = args_hash
        self._locked.add(op)

    async def release(self, op: str) -> None:
        self._locked.discard(op)


class RedisOperationStore:
    """Redis-backed store shared across gateway instances. ``client`` is injectable for tests."""

    def __init__(self, url: str | None = None, *, client=None) -> None:
        if client is not None:
            self._r = client
        else:
            from redis.asyncio import from_url

            if url is None:
                raise ValueError("redis url required when no client is provided")
            if url.startswith("rediss://"):
                import certifi

                self._r = from_url(url, decode_responses=True, ssl_ca_certs=certifi.where())
            else:
                self._r = from_url(url, decode_responses=True)

    async def reserve(self, op: str, args_hash: str) -> None:
        map_key = f"op:{op}"
        stored = await self._r.set(map_key, args_hash, nx=True, ex=MAP_TTL_SECONDS)
        if not stored:
            existing = await self._r.get(map_key)
            if existing is not None and existing != args_hash:
                raise BackendError(
                    E.IDEMPOTENCY_CONFLICT, "operation_id reused with a different payload"
                )
        got_lock = await self._r.set(f"lock:{op}", "1", nx=True, ex=LOCK_TTL_SECONDS)
        if not got_lock:
            raise BackendError(
                E.OPERATION_IN_PROGRESS, "operation already in progress", retryable=True
            )

    async def release(self, op: str) -> None:
        await self._r.delete(f"lock:{op}")


def build_operation_store(redis_url: str | None) -> OperationStore:
    return RedisOperationStore(redis_url) if redis_url else InMemoryOperationStore()


def _normalize_args(args: dict) -> dict:
    return {k: args[k] for k in sorted(args)}


def args_hash(principal_id: str, tool: str, args: dict) -> str:
    raw = json.dumps(
        {"p": principal_id, "t": tool, "a": _normalize_args(args)}, sort_keys=True, default=str
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _derive_op(principal_id: str, tool: str, args: dict) -> str:
    return "op-" + args_hash(principal_id, tool, args)[:24]


class Operations:
    """Maps a logical operation to a stable backend idempotency key via an OperationStore."""

    def __init__(self, store: OperationStore | None = None) -> None:
        self._store = store or InMemoryOperationStore()

    async def resolve(
        self, principal_id: str, tool: str, args: dict, operation_id: str | None
    ) -> tuple[str, str]:
        h = args_hash(principal_id, tool, args)
        op = operation_id or _derive_op(principal_id, tool, args)
        await self._store.reserve(op, h)
        idem = "idem-" + hashlib.sha256(op.encode()).hexdigest()[:32]
        return idem, op

    async def complete(self, op: str) -> None:
        await self._store.release(op)


# ---------------------------------------------------------------------------
# Payment gateway — orchestration (validate -> authorize -> resolve identity -> execute -> audit)
# ---------------------------------------------------------------------------


class PaymentGateway:
    """Trust boundary for payment operations.

    Money-correctness lives in the ledger; this layer governs what an agent may request. The
    principal is resolved per request (from a verified token in the HTTP path) with a constructor
    default for the stdio/demo path.
    """

    def __init__(
        self,
        backend: PaymentBackend,
        principal: AgentPrincipal,
        ops: Operations | None = None,
        merchant_id: str = "mcp-agent",
        mandate_verifier: MandateVerifier | None = None,
        mandate_authority: MandateAuthority | None = None,
    ) -> None:
        self.backend = backend
        self.principal = principal
        self.ops = ops or Operations()
        self.merchant_id = merchant_id
        self.mandates = mandate_verifier
        self.authority = mandate_authority

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
            _record_audit(
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
                m = self._verify_mandate_legacy(mandate)
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
            _record_audit(
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

    # ---- mandate integration ----
    def _verify_mandate_legacy(self, mandate: str) -> Mandate:
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


# Backward-compatible alias: existing code/tests imported the orchestrator as Gateway.
Gateway = PaymentGateway


__all__ = [
    "Gateway",
    "InMemoryOperationStore",
    "MAP_TTL_SECONDS",
    "LOCK_TTL_SECONDS",
    "OperationStore",
    "Operations",
    "PaymentGateway",
    "RedisOperationStore",
    "args_hash",
    "build_operation_store",
]
