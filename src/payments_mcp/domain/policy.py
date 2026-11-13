"""Deterministic policy checks; never delegate authorization to the language model."""

from payments_mcp.domain.errors import ApplicationError, ErrorCode


def enforce_bill_limit(amount_paise: int, maximum_paise: int) -> None:
    if amount_paise <= 0:
        raise ApplicationError(ErrorCode.INVALID_ARGUMENT, "Bill amount must be positive")
    if amount_paise > maximum_paise:
        raise ApplicationError(
            ErrorCode.AMOUNT_LIMIT_EXCEEDED,
            "Bill exceeds the configured single-payment limit",
        )


def require_registered_account(
    *,
    biller_id: str,
    requested_fingerprint: str,
    stored_biller_id: str,
    stored_fingerprint: str,
) -> None:
    if biller_id != stored_biller_id or requested_fingerprint != stored_fingerprint:
        raise ApplicationError(
            ErrorCode.ACCOUNT_NOT_REGISTERED,
            "This biller account is not registered",
        )
