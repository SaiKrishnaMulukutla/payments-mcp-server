"""H2: the operation store — conflict detection + fail-fast lock, in-memory and Redis.

The Redis tests use fakeredis with a SHARED server to simulate two gateway instances hitting the
same Redis, proving cross-instance conflict detection and concurrent-duplicate fail-fast.
"""

import fakeredis.aioredis
import pytest

from payments_mcp import errors as E
from payments_mcp.backend.base import BackendError
from payments_mcp.opstore import (
    InMemoryOperationStore,
    RedisOperationStore,
    build_operation_store,
)

H1 = "hash-aaa"
H2 = "hash-bbb"


# ---- in-memory ----
async def test_inmemory_reserve_then_release_allows_reuse():
    s = InMemoryOperationStore()
    await s.reserve("op-1", H1)
    await s.release("op-1")
    await s.reserve("op-1", H1)  # same payload after release -> ok (replay path)


async def test_inmemory_same_op_different_payload_conflicts():
    s = InMemoryOperationStore()
    await s.reserve("op-1", H1)
    with pytest.raises(BackendError) as ei:
        await s.reserve("op-1", H2)
    assert ei.value.code == E.IDEMPOTENCY_CONFLICT


async def test_inmemory_concurrent_duplicate_is_in_progress():
    s = InMemoryOperationStore()
    await s.reserve("op-1", H1)  # first holds the lock (no release yet)
    with pytest.raises(BackendError) as ei:
        await s.reserve("op-1", H1)
    assert ei.value.code == E.OPERATION_IN_PROGRESS and ei.value.retryable is True


# ---- redis (two stores sharing one fakeredis client = two gateway instances, one Redis) ----
def _two_instances():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return RedisOperationStore(client=client), RedisOperationStore(client=client)


async def test_redis_conflict_seen_by_other_instance():
    a, b = _two_instances()
    await a.reserve("op-9", H1)
    await a.release("op-9")  # free the lock so we isolate the CONFLICT check
    with pytest.raises(BackendError) as ei:
        await b.reserve("op-9", H2)  # different instance, same id, different payload
    assert ei.value.code == E.IDEMPOTENCY_CONFLICT


async def test_redis_concurrent_duplicate_across_instances_is_in_progress():
    a, b = _two_instances()
    await a.reserve("op-7", H1)  # instance A holds the lock (not released)
    with pytest.raises(BackendError) as ei:
        await b.reserve("op-7", H1)  # instance B sees it in-flight
    assert ei.value.code == E.OPERATION_IN_PROGRESS


async def test_redis_replay_after_release():
    a, _ = _two_instances()
    await a.reserve("op-5", H1)
    await a.release("op-5")
    await a.reserve("op-5", H1)  # same payload after release -> ok


def test_build_operation_store_selects_impl():
    assert isinstance(build_operation_store(None), InMemoryOperationStore)
