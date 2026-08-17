"""Postgres-backed, optimistic-concurrency approval repository.

``psycopg`` is intentionally imported only when this repository is constructed, allowing the
demo/test installation to stay dependency-light.
"""

from __future__ import annotations

from .models import ApprovalAction, ApprovalDecision, ApprovalRequest, ApprovalStatus


class PostgresApprovalRepository:
    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - exercised in deployment setup
            raise RuntimeError(
                "Postgres approval storage requires the payments-mcp[approvals] extra"
            ) from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self._database_url = database_url

    def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        query = """
            INSERT INTO payment_approvals (
                approval_id, tenant_id, requester_principal_id, action, amount_minor, currency,
                payer, payee, payment_id, status, decision, created_at, expires_at,
                updated_at, updated_by_principal_id, version
            ) VALUES (
                %(approval_id)s, %(tenant_id)s, %(requester_principal_id)s, %(action)s,
                %(amount_minor)s, %(currency)s, %(payer)s, %(payee)s, %(payment_id)s,
                %(status)s, %(decision)s, %(created_at)s, %(expires_at)s,
                %(updated_at)s, %(updated_by_principal_id)s, %(version)s
            ) RETURNING *
        """
        return self._execute_one(query, _params(approval))

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._execute_optional("SELECT * FROM payment_approvals WHERE approval_id = %s", (approval_id,))

    def list_pending(self, tenant_id: str) -> list[ApprovalRequest]:
        query = """
            SELECT * FROM payment_approvals
             WHERE tenant_id = %s AND status = 'PENDING'
             ORDER BY created_at ASC
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(query, (tenant_id,))
            return [_from_row(row) for row in cursor.fetchall()]

    def transition(
        self,
        approval_id: str,
        *,
        expected: ApprovalStatus,
        expected_version: int,
        updated: ApprovalRequest,
    ) -> ApprovalRequest:
        query = """
            UPDATE payment_approvals
               SET status = %(status)s,
                   decision = %(decision)s,
                   decided_at = %(decided_at)s,
                   decided_by_principal_id = %(decided_by_principal_id)s,
                   updated_at = %(updated_at)s,
                   updated_by_principal_id = %(updated_by_principal_id)s,
                   consumed_at = %(consumed_at)s,
                   mandate_id = %(mandate_id)s,
                   version = version + 1
             WHERE approval_id = %(approval_id)s
               AND status = %(expected_status)s
               AND version = %(expected_version)s
         RETURNING *
        """
        params = _params(updated) | {
            "expected_status": expected.value,
            "expected_version": expected_version,
        }
        result = self._execute_optional(query, params)
        if result is None:
            raise ValueError("approval state changed; reload before retrying")
        return result

    def _connect(self):
        return self._psycopg.connect(self._database_url, row_factory=self._dict_row)

    def _execute_one(self, query: str, params: dict) -> ApprovalRequest:
        result = self._execute_optional(query, params)
        if result is None:  # pragma: no cover - INSERT RETURNING always returns a row
            raise RuntimeError("approval insert returned no row")
        return result

    def _execute_optional(self, query: str, params) -> ApprovalRequest | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
            return _from_row(row) if row is not None else None


def _params(approval: ApprovalRequest) -> dict:
    result = approval.model_dump()
    result["action"] = approval.action.value
    result["status"] = approval.status.value
    result["decision"] = approval.decision.value if approval.decision else None
    return result


def _from_row(row: dict) -> ApprovalRequest:
    values = dict(row)
    values["action"] = ApprovalAction(values["action"])
    values["status"] = ApprovalStatus(values["status"])
    values["decision"] = ApprovalDecision(values["decision"]) if values["decision"] else None
    return ApprovalRequest(**values)
