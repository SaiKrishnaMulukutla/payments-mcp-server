"""Audit trail: correlate agent intent with backend execution.

Logs to a logger (stderr) so it never corrupts the stdio MCP protocol on stdout. Raw arguments
are never logged — only a hash — so sensitive values don't leak into logs.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("payments_mcp.audit")


def record(
    *,
    correlation_id: str,
    principal_id: str,
    tool: str,
    operation_id: str | None,
    args_hash: str,
    policy_decision: str,
    result_code: str,
    latency_ms: int,
    backend_resource_id: str | None = None,
) -> None:
    logger.info(
        "AUDIT %s",
        json.dumps(
            {
                "correlation_id": correlation_id,
                "principal_id": principal_id,
                "tool": tool,
                "operation_id": operation_id,
                "args_hash": args_hash,
                "policy_decision": policy_decision,
                "backend_resource_id": backend_resource_id,
                "result_code": result_code,
                "latency_ms": latency_ms,
            }
        ),
    )
