"""Structured audit event model. Persist events transactionally with state changes."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str
    action: str
    resource_id: str | None = None
    correlation_id: str
    outcome: str
    metadata: dict[str, Any] = Field(default_factory=dict)
