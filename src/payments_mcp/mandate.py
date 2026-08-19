"""Mandate domain — the authority boundary.

Everything about delegated authority lives here: proposals, human approval/rejection,
signing, verification, revocation, status, constraints and aggregate limits.

Important separation
--------------------
``console.py`` (and the HTTP console) never decide whether something is authorized. They only
report a human decision (APPROVE / REJECT / REVOKE). This module performs the actual state
transition and signing logic.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

from . import errors as E
from .errors import BackendError
from .models import (
    AggregatePeriod,
    Mandate,
    MandateOperation,
    MandateProposal,
    MandateStatus,
    MandateTerms,
    ProposalApprovalStatus,
    ReusableMandate,
)

# Backward-compatible status constants (legacy issuer API).
PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

# ---------------------------------------------------------------------------
# Legacy token signing + verification (single-payment compatible)
# ---------------------------------------------------------------------------


def sign_mandate(
    claims: dict, secret: str, ttl_seconds: int = 900, issuer: str | None = None
) -> str:
    """Development helper. Production signing will be isolated from MCP execution."""
    import jwt

    payload = {**claims, "exp": int(time.time()) + ttl_seconds}
    if issuer:
        payload["iss"] = issuer
    return jwt.encode(payload, secret, algorithm="HS256")


class MandateVerifier:
    """Signing and verification of the legacy mandate-token format.

    The signer/verifier interface will move to asymmetric keys before the reusable mandate flow is
    enabled in production. This implementation is retained for local development compatibility.
    """

    def __init__(self, secret: str, issuer: str | None = None) -> None:
        self._secret = secret
        self._issuer = issuer

    def verify(self, token: str) -> Mandate:
        import jwt

        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                options={"require": ["exp", "mandate_id"], "verify_aud": False},
            )
        except Exception as e:  # noqa: BLE001
            raise BackendError(E.MANDATE_INVALID, "mandate missing, invalid, or expired") from e
        return Mandate(
            mandate_id=str(claims["mandate_id"]),
            payer=str(claims["payer"]),
            payee=str(claims["payee"]),
            currency=str(claims["currency"]),
            max_amount_minor=int(claims["max_amount_minor"]),
        )

    def enforce(
        self, m: Mandate, *, payer: str, payee: str, currency: str, amount_minor: int
    ) -> None:
        if payer != m.payer or payee != m.payee or currency != m.currency:
            raise BackendError(E.MANDATE_MISMATCH, "payment parties do not match the mandate")
        if amount_minor > m.max_amount_minor:
            raise BackendError(E.MANDATE_MISMATCH, "amount exceeds the mandate's authorized maximum")


# ---------------------------------------------------------------------------
# Mandate authority — the domain class the console/server depend on
# ---------------------------------------------------------------------------


class MandateAuthority:
    """Single entry point for the whole mandate domain.

    Holds the signing key, tracks proposals and active mandates, performs state transitions
    (approve/reject/revoke/expire), and enforces terms + aggregate limits before execution.
    """

    def __init__(
        self,
        secret: str,
        issuer: str | None = None,
        *,
        ttl_seconds: int = 900,
        signing_key_id: str = "dev-hs256",
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("mandate proposal ttl must be positive")
        self._secret = secret
        self._issuer = issuer
        self._ttl = timedelta(seconds=ttl_seconds)
        self._signing_key_id = signing_key_id
        self._proposals: dict[str, MandateProposal] = {}
        self._mandates: dict[str, ReusableMandate] = {}

    # ------------------------------------------------------------------
    # Proposals
    # ------------------------------------------------------------------

    def propose(self, *, tenant_id: str, requester_principal_id: str, terms: MandateTerms) -> MandateProposal:
        """Agent-propose authority; stays PENDING until a human approves or rejects."""
        now = datetime.now(timezone.utc)
        proposal = MandateProposal(
            proposal_id="prop-" + uuid.uuid4().hex[:12],
            tenant_id=tenant_id,
            requester_principal_id=requester_principal_id,
            terms=terms,
            approval_status=ProposalApprovalStatus.PENDING,
            created_at=now,
            updated_at=now,
            updated_by_principal_id=requester_principal_id,
        )
        self._proposals[proposal.proposal_id] = proposal
        return proposal

    def get(self, proposal_id: str) -> MandateProposal | None:
        proposal = self._proposals.get(proposal_id)
        return self._expire_if_needed(proposal) if proposal is not None else None

    def pending(self) -> list[MandateProposal]:
        return [
            self._expire_if_needed(proposal)
            for proposal in self._proposals.values()
            if proposal.approval_status == ProposalApprovalStatus.PENDING
        ]

    def approve_proposal(self, proposal_id: str, *, approver_principal_id: str) -> ReusableMandate:
        """Human APPROVE -> state transition + signing. Returns the signed reusable mandate."""
        proposal = self._require_pending(proposal_id)
        now = datetime.now(timezone.utc)
        updated = proposal.model_copy(
            update={
                "approval_status": ProposalApprovalStatus.APPROVED,
                "decided_at": now,
                "decided_by_principal_id": approver_principal_id,
                "updated_at": now,
                "updated_by_principal_id": approver_principal_id,
            }
        )
        self._transition_proposal(proposal_id, updated)
        return self._issue_mandate(updated, approved_by=approver_principal_id)

    def reject_proposal(self, proposal_id: str, *, approver_principal_id: str) -> MandateProposal:
        proposal = self._require_pending(proposal_id)
        now = datetime.now(timezone.utc)
        updated = proposal.model_copy(
            update={
                "approval_status": ProposalApprovalStatus.REJECTED,
                "decided_at": now,
                "decided_by_principal_id": approver_principal_id,
                "updated_at": now,
                "updated_by_principal_id": approver_principal_id,
            }
        )
        return self._transition_proposal(proposal_id, updated)

    # ------------------------------------------------------------------
    # Active mandates + revocation
    # ------------------------------------------------------------------

    def active(self) -> list[ReusableMandate]:
        return [
            mandate
            for mandate in self._mandates.values()
            if mandate.mandate_status == MandateStatus.ACTIVE
        ]

    def get_mandate(self, mandate_id: str) -> ReusableMandate | None:
        mandate = self._mandates.get(mandate_id)
        return self._expire_mandate_if_needed(mandate) if mandate is not None else None

    def revoke_mandate(self, mandate_id: str, *, revoker_principal_id: str) -> ReusableMandate:
        mandate = self._require_active(mandate_id)
        now = datetime.now(timezone.utc)
        updated = mandate.model_copy(
            update={
                "mandate_status": MandateStatus.REVOKED,
                "revoked_at": now,
            }
        )
        self._mandates[mandate_id] = updated
        return updated

    # ------------------------------------------------------------------
    # Verification + enforcement of reusable mandates
    # ------------------------------------------------------------------

    def verify(self, token: str) -> ReusableMandate:
        """Verify a signed reusable-mandate artifact. The artifact embeds its id; we then load
        the persisted authority record so revocation/expiry are honored."""
        import jwt

        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                options={"require": ["exp", "terms"], "verify_aud": False},
            )
        except Exception as e:  # noqa: BLE001
            raise BackendError(E.MANDATE_INVALID, "mandate missing, invalid, or expired") from e
        terms = MandateTerms(**claims["terms"])
        mandate = self._mandates.get(str(claims.get("mandate_id", "")))
        if mandate is None:
            mandate = ReusableMandate(
                mandate_id=str(claims["jti"]),
                proposal_id=str(claims.get("proposal_id", "")),
                tenant_id=str(claims.get("tenant_id", "")),
                requester_principal_id=str(claims.get("sub", "")),
                terms=terms,
                mandate_status=MandateStatus.ACTIVE,
                signed_artifact=token,
                signing_key_id=self._signing_key_id,
                issued_at=datetime.now(timezone.utc),
            )
        return self._expire_mandate_if_needed(mandate)

    def enforce(
        self,
        mandate: ReusableMandate,
        *,
        payer: str,
        payee: str,
        currency: str,
        amount_minor: int,
        aggregate_spent_minor: int = 0,
    ) -> None:
        """Enforce the reusable mandate's terms against a proposed exercise."""
        m = self._expire_mandate_if_needed(mandate)
        if m.mandate_status != MandateStatus.ACTIVE:
            raise BackendError(E.MANDATE_INVALID, f"mandate is {m.mandate_status.value}")
        terms = m.terms
        if payer != terms.payer_account_id:
            raise BackendError(E.MANDATE_MISMATCH, "payer does not match the mandate")
        if payee not in terms.allowed_payee_ids:
            raise BackendError(E.MANDATE_MISMATCH, "payee is not allowed by the mandate")
        if currency != terms.currency:
            raise BackendError(E.MANDATE_MISMATCH, "currency does not match the mandate")
        if amount_minor > terms.per_transaction_limit_minor:
            raise BackendError(E.MANDATE_MISMATCH, "amount exceeds the per-transaction limit")
        if amount_minor > terms.approval_required_above_minor and terms.approval_required_above_minor > 0:
            raise BackendError(
                E.APPROVAL_REQUIRED, "amount requires a separate human approval for this mandate"
            )
        if aggregate_spent_minor + amount_minor > terms.aggregate_limit_minor:
            raise BackendError(E.MANDATE_MISMATCH, "aggregate limit would be exceeded")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _issue_mandate(self, proposal: MandateProposal, *, approved_by: str) -> ReusableMandate:
        now = datetime.now(timezone.utc)
        terms = proposal.terms
        signed_artifact = sign_mandate(
            {
                "jti": f"mnd-" + uuid.uuid4().hex[:12],
                "sub": proposal.requester_principal_id,
                "proposal_id": proposal.proposal_id,
                "tenant_id": proposal.tenant_id,
                "terms": terms.model_dump(mode="json"),
            },
            self._secret,
            max(1, int((terms.expires_at - now).total_seconds())),
            self._issuer,
        )
        mandate = ReusableMandate(
            mandate_id=f"mnd-{uuid.uuid4().hex[:12]}",
            proposal_id=proposal.proposal_id,
            tenant_id=proposal.tenant_id,
            requester_principal_id=proposal.requester_principal_id,
            terms=terms,
            mandate_status=MandateStatus.ACTIVE,
            signed_artifact=signed_artifact,
            signing_key_id=self._signing_key_id,
            issued_at=now,
        )
        self._mandates[mandate.mandate_id] = mandate
        return mandate

    def _require_pending(self, proposal_id: str) -> MandateProposal:
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal.approval_status != ProposalApprovalStatus.PENDING:
            raise ValueError(f"proposal is {proposal.approval_status.value}")
        return proposal

    def _require_active(self, mandate_id: str) -> ReusableMandate:
        mandate = self.get_mandate(mandate_id)
        if mandate is None:
            raise KeyError(mandate_id)
        if mandate.mandate_status != MandateStatus.ACTIVE:
            raise ValueError(f"mandate is {mandate.mandate_status.value}")
        return mandate

    def _transition_proposal(
        self, proposal_id: str, updated: MandateProposal
    ) -> MandateProposal:
        current = self._proposals.get(proposal_id)
        if current is None:
            raise KeyError(proposal_id)
        if current.version != updated.version:
            raise ValueError("proposal state changed; reload before retrying")
        persisted = updated.model_copy(update={"version": current.version + 1})
        self._proposals[proposal_id] = persisted
        return persisted

    def _expire_if_needed(self, proposal: MandateProposal) -> MandateProposal:
        if (
            proposal.approval_status != ProposalApprovalStatus.PENDING
            or proposal.created_at + self._ttl > datetime.now(timezone.utc)
        ):
            return proposal
        updated = proposal.model_copy(
            update={
                "approval_status": ProposalApprovalStatus.REJECTED,
                "updated_at": datetime.now(timezone.utc),
                "updated_by_principal_id": "mandate-authority",
            }
        )
        return self._transition_proposal(proposal.proposal_id, updated)

    def _expire_mandate_if_needed(self, mandate: ReusableMandate) -> ReusableMandate:
        if mandate.mandate_status == MandateStatus.ACTIVE and mandate.terms.expires_at <= datetime.now(
            timezone.utc
        ):
            updated = mandate.model_copy(update={"mandate_status": MandateStatus.EXPIRED})
            self._mandates[mandate.mandate_id] = updated
            return updated
        return mandate


__all__ = [
    "APPROVED",
    "MandateAuthority",
    "MandateVerifier",
    "PENDING",
    "REJECTED",
    "sign_mandate",
]
