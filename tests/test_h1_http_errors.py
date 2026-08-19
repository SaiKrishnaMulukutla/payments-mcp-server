"""H1: HttpPaymentBackend maps wallet-service HTTP/ProblemDetail statuses to the right taxonomy.

Regression guard for the 409/422 mapping fix: 409 (idempotency key IN_PROGRESS) is a transient,
retryable OPERATION_IN_PROGRESS — not a hard IDEMPOTENCY_CONFLICT; the genuine "same key, different
body" case (422) is the conflict.
"""

import httpx
import pytest
import respx

from payments_mcp import errors as E
from payments_mcp.backend import HttpPaymentBackend
from payments_mcp.config import Settings
from payments_mcp.errors import BackendError, new_correlation_id, to_gateway_error

BASE = "http://backend:8080"


def _backend() -> HttpPaymentBackend:
    return HttpPaymentBackend(Settings(backend="http", base_url=BASE))


def _problem(detail: str) -> dict:
    return {"detail": detail}


async def _create(b: HttpPaymentBackend):
    return await b.create_payment(
        idempotency_key="idem-1", payer="acct-A", payee="acct-B", amount_minor=500, currency="INR"
    )


@respx.mock
async def test_409_in_progress_is_retryable_operation_in_progress():
    respx.post(f"{BASE}/v1/payments").mock(
        return_value=httpx.Response(
            409, json=_problem("a request with idempotency key 'idem-1' is already in progress")
        )
    )
    b = _backend()
    with pytest.raises(BackendError) as ei:
        await _create(b)
    assert ei.value.code == E.OPERATION_IN_PROGRESS
    assert ei.value.retryable is True
    ge = to_gateway_error(ei.value, new_correlation_id())
    assert ge.retryable is True and ge.retry_after_seconds  # tells the agent to wait and re-check
    await b.aclose()


@respx.mock
async def test_422_different_body_is_idempotency_conflict():
    respx.post(f"{BASE}/v1/payments").mock(
        return_value=httpx.Response(
            422,
            json=_problem("idempotency key 'idem-1' was already used with a different request body"),
        )
    )
    b = _backend()
    with pytest.raises(BackendError) as ei:
        await _create(b)
    assert ei.value.code == E.IDEMPOTENCY_CONFLICT
    assert ei.value.retryable is False
    await b.aclose()


@respx.mock
async def test_422_insufficient_funds():
    respx.post(f"{BASE}/v1/payments").mock(
        return_value=httpx.Response(
            422, json=_problem("insufficient funds in account acct-A: balance=100, attempted debit=500")
        )
    )
    b = _backend()
    with pytest.raises(BackendError) as ei:
        await _create(b)
    assert ei.value.code == E.INSUFFICIENT_FUNDS
    await b.aclose()


@respx.mock
async def test_422_other_is_invalid_argument():
    respx.post(f"{BASE}/v1/payments").mock(
        return_value=httpx.Response(422, json=_problem("payment is not refundable in status FAILED"))
    )
    b = _backend()
    with pytest.raises(BackendError) as ei:
        await _create(b)
    assert ei.value.code == E.INVALID_ARGUMENT
    await b.aclose()


@respx.mock
async def test_5xx_and_timeout_are_retryable():
    route = respx.post(f"{BASE}/v1/payments")
    route.mock(return_value=httpx.Response(503))
    b = _backend()
    with pytest.raises(BackendError) as ei:
        await _create(b)
    assert ei.value.code == E.BACKEND_UNAVAILABLE and ei.value.retryable is True

    route.mock(side_effect=httpx.ConnectTimeout("boom"))
    with pytest.raises(BackendError) as ei:
        await _create(b)
    assert ei.value.code == E.BACKEND_TIMEOUT and ei.value.retryable is True
    await b.aclose()
</｜DSML｜parameter>
</write_to_file>