"""M2 tests: capability policy (scope + account access)."""

import pytest

from payments_mcp import errors as E
from payments_mcp import policy
from payments_mcp.backend.base import BackendError
from payments_mcp.config import AgentPrincipal


def test_require_scope_denies_missing():
    p = AgentPrincipal(scopes=["payments:read"])
    policy.require_scope(p, policy.READ)  # ok
    with pytest.raises(BackendError) as ei:
        policy.require_scope(p, policy.CREATE)
    assert ei.value.code == E.PERMISSION_DENIED


def test_require_account_respects_allowlist():
    p = AgentPrincipal(allowed_accounts=["acct-A"])
    policy.require_account(p, "acct-A")  # ok
    with pytest.raises(BackendError) as ei:
        policy.require_account(p, "acct-Z")
    assert ei.value.code == E.ACCOUNT_NOT_ALLOWED


def test_empty_allowlist_is_unrestricted():
    p = AgentPrincipal(allowed_accounts=[])
    policy.require_account(p, "anything")  # no raise
