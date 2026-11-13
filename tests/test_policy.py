from payments_mcp.domain.errors import ApplicationError, ErrorCode
from payments_mcp.domain.policy import enforce_bill_limit, require_registered_account


def test_bill_limit_accepts_boundary() -> None:
    enforce_bill_limit(1_000_000, 1_000_000)


def test_bill_limit_rejects_over_limit() -> None:
    try:
        enforce_bill_limit(1_000_001, 1_000_000)
    except ApplicationError as exc:
        assert exc.code is ErrorCode.AMOUNT_LIMIT_EXCEEDED
    else:
        raise AssertionError("expected amount limit error")


def test_registered_account_requires_biller_and_fingerprint_match() -> None:
    require_registered_account(
        biller_id="biller-1",
        requested_fingerprint="abc",
        stored_biller_id="biller-1",
        stored_fingerprint="abc",
    )


def test_registered_account_rejects_wrong_fingerprint() -> None:
    try:
        require_registered_account(
            biller_id="biller-1",
            requested_fingerprint="wrong",
            stored_biller_id="biller-1",
            stored_fingerprint="abc",
        )
    except ApplicationError as exc:
        assert exc.code is ErrorCode.ACCOUNT_NOT_REGISTERED
    else:
        raise AssertionError("expected account allowlist error")
