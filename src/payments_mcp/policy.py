"""Capability policy: scope + account-access checks (least privilege).

Failures raise BackendError with a taxonomy code so they normalize through errors.to_gateway_error
exactly like backend failures — one error path for the agent.
"""

from __future__ import annotations

from . import errors as E
from .backend.base import BackendError
from .config import AgentPrincipal

READ = "payments:read"
CREATE = "payments:create"
REFUND = "payments:refund"
ADMIN = "payments:admin"


def require_scope(principal: AgentPrincipal, scope: str) -> None:
    if not principal.has_scope(scope):
        raise BackendError(E.PERMISSION_DENIED, f"agent lacks required scope: {scope}")


def require_account(principal: AgentPrincipal, account_id: str) -> None:
    if not principal.allows_account(account_id):
        raise BackendError(E.ACCOUNT_NOT_ALLOWED, f"account outside allow-list: {account_id}")


# ---- semantic validation (beyond JSON schema) ----
ALLOWED_CURRENCIES = ("INR", "USD")


def validate_amount(amount_minor: int) -> None:
    if amount_minor <= 0 or amount_minor > 1_000_000_000:
        raise BackendError(E.INVALID_ARGUMENT, "amount must be a positive number of minor units")


def validate_currency(currency: str) -> None:
    if currency.upper() not in ALLOWED_CURRENCIES:
        raise BackendError(E.INVALID_ARGUMENT, f"currency must be one of {ALLOWED_CURRENCIES}")


def require_within_limit(principal: AgentPrincipal, amount_minor: int) -> None:
    """Human-in-the-loop boundary: amounts over the autonomous ceiling need external approval."""
    if amount_minor > principal.max_payment_amount_minor:
        raise BackendError(
            E.APPROVAL_REQUIRED,
            f"amount exceeds the agent's autonomous limit "
            f"({principal.max_payment_amount_minor} minor units)",
        )
