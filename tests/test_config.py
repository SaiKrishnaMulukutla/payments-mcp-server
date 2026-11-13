import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from payments_mcp.config import Settings


def test_pool_size_must_be_valid() -> None:
    with pytest.raises(ValidationError, match="DATABASE_POOL_MIN_SIZE"):
        Settings(
            database_url="postgresql://localhost/test",
            account_encryption_key=Fernet.generate_key().decode(),
            approval_signing_secret="x" * 48,
            account_fingerprint_secret="y" * 48,
            database_pool_min_size=6,
            database_pool_max_size=2,
        )


def test_production_cashfree_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql://localhost/test",
            account_encryption_key=Fernet.generate_key().decode(),
            approval_signing_secret="x" * 48,
            account_fingerprint_secret="y" * 48,
            cashfree_environment="production",
        )
