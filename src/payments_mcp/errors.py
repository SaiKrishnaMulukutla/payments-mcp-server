"""Agent-oriented error taxonomy + normalization.

Backend failures (BackendError, HTTP problems) are normalized into a stable, model-readable
shape: {code, message, retryable, suggested_action, correlation_id}. `suggested_action` is
chosen from a gateway-owned map — never echoed from backend data (prompt-injection guard).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from .backend.base import BackendError

# ---- taxonomy ----
INVALID_ARGUMENT = "INVALID_ARGUMENT"
PERMISSION_DENIED = "PERMISSION_DENIED"
ACCOUNT_NOT_ALLOWED = "ACCOUNT_NOT_ALLOWED"
PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"
ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
OPERATION_IN_PROGRESS = "OPERATION_IN_PROGRESS"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
RATE_LIMITED = "RATE_LIMITED"
BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"

# ---- gateway-owned suggested actions (deterministic; never from backend text) ----
SUGGESTED_ACTIONS: dict[str, str] = {
    INVALID_ARGUMENT: "Fix the arguments and try again.",
    PERMISSION_DENIED: "This agent lacks the scope for that operation; do not retry.",
    ACCOUNT_NOT_ALLOWED: "This account is outside the agent's allow-list; ask the user.",
    PAYMENT_NOT_FOUND: "Do not fabricate a payment id; ask the user for a valid one.",
    ACCOUNT_NOT_FOUND: "Confirm the account id with the user.",
    INSUFFICIENT_FUNDS: "Ask the user to choose another payer or reduce the amount.",
    IDEMPOTENCY_CONFLICT: "Same operation id was used with a different payload; use a new operation.",
    OPERATION_IN_PROGRESS: "The operation is already running; wait and re-check, do not resubmit.",
    APPROVAL_REQUIRED: "This exceeds the agent's autonomous limit; request human approval.",
    RATE_LIMITED: "Slow down and retry after a short delay.",
    BACKEND_TIMEOUT: "Transient timeout; a bounded retry is safe.",
    BACKEND_UNAVAILABLE: "Backend is temporarily unavailable; retry after a short delay.",
    INTERNAL_ERROR: "Unexpected gateway error; do not retry blindly.",
}


class GatewayError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    suggested_action: str | None = None
    retry_after_seconds: int | None = None
    correlation_id: str

    def to_dict(self) -> dict:
        return self.model_dump()


def new_correlation_id() -> str:
    return "corr_" + uuid.uuid4().hex[:16]


def to_gateway_error(exc: Exception, correlation_id: str) -> GatewayError:
    """Normalize any failure into the agent-facing error shape."""
    if isinstance(exc, BackendError):
        code, message, retryable = exc.code, exc.message, exc.retryable
    else:
        code, message, retryable = INTERNAL_ERROR, "internal gateway error", False
    return GatewayError(
        code=code,
        message=message,
        retryable=retryable,
        suggested_action=SUGGESTED_ACTIONS.get(code),
        retry_after_seconds=5 if retryable else None,
        correlation_id=correlation_id,
    )
