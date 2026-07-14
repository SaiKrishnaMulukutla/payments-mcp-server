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

from . import errors as E
from .backend.base import BackendError


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
    """In-gateway registry mapping operation_id -> args_hash, to detect payload conflicts."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def resolve(
        self, principal_id: str, tool: str, args: dict, operation_id: str | None
    ) -> tuple[str, str]:
        """Return (idempotency_key, operation_id). Raises IDEMPOTENCY_CONFLICT on payload reuse."""
        h = args_hash(principal_id, tool, args)
        op = operation_id or _derive_op(principal_id, tool, args)
        prev = self._seen.get(op)
        if prev is not None and prev != h:
            raise BackendError(
                E.IDEMPOTENCY_CONFLICT, "operation_id reused with a different payload"
            )
        self._seen[op] = h
        idem = "idem-" + hashlib.sha256(op.encode()).hexdigest()[:32]
        return idem, op
