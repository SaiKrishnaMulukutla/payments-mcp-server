"""Logical-operation identity across a probabilistic caller — the project centerpiece.

The backend's idempotency only protects retries carrying the SAME key. A flaky agent may retry
and generate a new key -> a second payment. The gateway fixes that:
  * accept an `operation_id` if the caller supplies one,
  * otherwise DERIVE one deterministically from (principal, tool, normalized args),
then map the operation to a STABLE backend idempotency key. Reusing an operation_id with a
DIFFERENT payload is a conflict, rejected before the backend is ever called.
"""

from __future__ import annotations

import hashlib
import json

from .opstore import InMemoryOperationStore, OperationStore


def _normalize(args: dict) -> dict:
    return {k: args[k] for k in sorted(args)}


def args_hash(principal_id: str, tool: str, args: dict) -> str:
    raw = json.dumps(
        {"p": principal_id, "t": tool, "a": _normalize(args)}, sort_keys=True, default=str
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
        """Return (idempotency_key, operation_id) and reserve the operation."""
        h = args_hash(principal_id, tool, args)
        op = operation_id or _derive_op(principal_id, tool, args)
        await self._store.reserve(op, h)
        idem = "idem-" + hashlib.sha256(op.encode()).hexdigest()[:32]
        return idem, op

    async def complete(self, op: str) -> None:
        await self._store.release(op)
