"""Approval persistence boundary.

The in-memory implementation is for tests and local demos only. A transactional Postgres
implementation will satisfy this protocol in the next increment.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ApprovalRequest, ApprovalStatus


@runtime_checkable
class ApprovalRepository(Protocol):
    def create(self, approval: ApprovalRequest) -> ApprovalRequest: ...

    def get(self, approval_id: str) -> ApprovalRequest | None: ...

    def list_pending(self, tenant_id: str) -> list[ApprovalRequest]: ...

    def transition(
        self,
        approval_id: str,
        *,
        expected: ApprovalStatus,
        expected_version: int,
        updated: ApprovalRequest,
    ) -> ApprovalRequest: ...
