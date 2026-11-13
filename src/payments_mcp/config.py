"""Validated runtime configuration. Secrets are required when constructing the app."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    database_url: SecretStr
    account_encryption_key: SecretStr
    approval_signing_secret: SecretStr = Field(min_length=32)
    account_fingerprint_secret: SecretStr = Field(min_length=32)
    cashfree_client_id: SecretStr | None = None
    cashfree_client_secret: SecretStr | None = None
    cashfree_environment: Literal["sandbox"] = "sandbox"
    max_bill_amount_paise: int = Field(default=1_000_000, gt=0)
    approval_ttl_seconds: int = Field(default=300, ge=30, le=900)
    database_pool_min_size: int = Field(default=1, ge=1)
    database_pool_max_size: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def validate_pool(self) -> "Settings":
        if self.database_pool_min_size > self.database_pool_max_size:
            raise ValueError("DATABASE_POOL_MIN_SIZE cannot exceed DATABASE_POOL_MAX_SIZE")
        if (self.cashfree_client_id is None) != (self.cashfree_client_secret is None):
            raise ValueError("Cashfree client ID and secret must be configured together")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings lazily so importing domain modules needs no environment secrets."""
    return Settings()  # type: ignore[call-arg]  # Values are loaded from environment / .env.
