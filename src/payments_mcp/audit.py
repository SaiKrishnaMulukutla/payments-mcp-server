"""Structured audit event shape. Persistence and redaction are implemented with repository use cases."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    occurred_at: datetime = datetime.now(timezone.utc)
    actor: str
    action: str
    resource_id: str | None = None
    correlation_id: str
    outcome: str
    metadata: dict[str, Any] = {}
