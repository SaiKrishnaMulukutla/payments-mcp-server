"""Gateway configuration + the agent principal (capability scope)."""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env-driven config. Prefix PAYMENTS_ (e.g. PAYMENTS_BACKEND=http)."""

    model_config = SettingsConfigDict(env_prefix="PAYMENTS_", env_file=".env", extra="ignore")

    backend: str = "demo"  # "demo" | "http"
    base_url: str = "http://localhost:8080"
    token: str | None = None  # bearer for the backend; NEVER exposed to the model
    request_timeout_s: float = 10.0
    merchant_id: str = "mcp-agent"  # gateway-owned; not agent-controlled


class AgentPrincipal(BaseModel):
    """Who the agent is allowed to be. Least-privilege: scopes + account allow-list + ceiling."""

    principal_id: str = "demo-agent"
    allowed_accounts: list[str] = Field(default_factory=list)  # empty => unrestricted (dev only)
    scopes: list[str] = Field(default_factory=lambda: ["payments:read"])
    max_payment_amount_minor: int = 10_000  # autonomous ceiling (₹100 demo)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def allows_account(self, account_id: str) -> bool:
        return not self.allowed_accounts or account_id in self.allowed_accounts


def demo_principal() -> AgentPrincipal:
    """A deliberately-scoped principal for the demo: reads + small payments on A/B, no refunds/admin."""
    return AgentPrincipal(
        principal_id="demo-agent",
        allowed_accounts=["acct-A", "acct-B"],
        scopes=["payments:read", "payments:create"],
        max_payment_amount_minor=10_000,
    )
