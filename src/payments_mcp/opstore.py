"""Shared conflict map + fail-fast lock behind a protocol.

Best-effort early guard only; the backend ledger's durable idempotency stays the source of truth.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from . import errors as E
from .backend.base import BackendError

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

            kwargs = {"decode_responses": True}
            if url and url.startswith("rediss://"):
                import certifi

                kwargs["ssl_ca_certs"] = certifi.where()
            self._r = from_url(url, **kwargs)

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
