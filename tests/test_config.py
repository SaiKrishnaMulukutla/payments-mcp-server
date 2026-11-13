import pytest
from pydantic import ValidationError

from payments_mcp.config import Settings


def test_pool_size_must_be_valid() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql://localhost/test",
            account_encryption_key="not-a-fernet-key",
            approval_signing_secret="x" * 48,
            database_pool_min_size=6,
            database_pool_max_size=2,
        )
