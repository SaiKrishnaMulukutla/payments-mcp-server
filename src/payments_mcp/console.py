"""Human decision console (CLI).

Presents pending proposals and active mandates to a human; relays the human's choice
(APPROVE / REJECT / REVOKE) to :class:`payments_mcp.mandate.MandateAuthority`.

This module NEVER decides whether something is authorized — it only says "the human selected
APPROVE" and lets ``mandate.py`` perform the actual state transition and signing logic.

Run with::

    python -m payments_mcp.console
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from .mandate import MandateAuthority
from .models import MandateOperation, MandateTerms


def _fmt_minor(amount_minor: int) -> str:
    return f"₹{amount_minor / 100:,.2f}"


def _fmt_currency(currency: str) -> str:
    return "INR" if currency == "INR" else currency


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%b %d")


def _print_proposal(proposal) -> None:
    terms = proposal.terms
    print("Pending Mandate")
    print("────────────────────────")
    print(f"Payer:       {terms.payer_account_id}")
    print(f"Payees:      {', '.join(terms.allowed_payee_ids)}")
    print(f"Currency:    {_fmt_currency(terms.currency)}")
    print("")
    print(f"Per payment: {_fmt_minor(terms.per_transaction_limit_minor)}")
    print(f"Monthly:     {_fmt_minor(terms.aggregate_limit_minor)}")
    print(f"Valid until: {_fmt_date(terms.expires_at)}")
    print("")
    print("[ A ] Approve")
    print("[ R ] Reject")


def _print_active(mandate) -> None:
    terms = mandate.terms
    print("Mandate ACTIVE")
    print("────────────────────────")
    print(f"ID:          {mandate.mandate_id}")
    print(f"Payer:       {terms.payer_account_id}")
    print(f"Payees:      {', '.join(terms.allowed_payee_ids)}")
    print(f"Currency:    {_fmt_currency(terms.currency)}")
    print(f"Per payment: {_fmt_minor(terms.per_transaction_limit_minor)}")
    print(f"Monthly:     {_fmt_minor(terms.aggregate_limit_minor)}")
    print(f"Valid until: {_fmt_date(terms.expires_at)}")
    print("")
    print("[ R ] Revoke")
    print("[ B ] Back")


def _print_revoked() -> None:
    print("Mandate REVOKED ✓")


def main(argv: list[str] | None = None) -> int:
    """Interactive mandate console loop."""
    argv = argv if argv is not None else sys.argv[1:]
    secret = "console-dev-secret-0123456789ab"
    authority = MandateAuthority(secret)

    # Seed a demo proposal so the CLI has something to show immediately.
    if "--no-seed" not in argv:
        now = datetime.now(timezone.utc)
        authority.propose(
            tenant_id="demo-tenant",
            requester_principal_id="demo-agent",
            terms=MandateTerms(
                payer_account_id="acct-A",
                allowed_payee_ids=["acct-B"],
                allowed_operations=[MandateOperation.PAY_BILL],
                currency="INR",
                purpose="Electricity",
                per_transaction_limit_minor=300_00,
                aggregate_limit_minor=10_00_00,
                aggregate_period="MONTHLY",
                approval_required_above_minor=0,
                valid_from=now,
                expires_at=now.replace(year=now.year + 1),
            ),
        )

    while True:
        print("")
        print("=== Mandate Console ===")
        print("")
        print("1. Pending proposals")
        print("2. Active mandates")
        print("3. Revoke mandate")
        print("4. Exit")
        print("")
        choice = input("> ").strip()
        if choice == "1":
            pending = authority.pending()
            if not pending:
                print("No pending proposals.")
                continue
            for i, proposal in enumerate(pending, start=1):
                print(f"\n[{i}]")
                _print_proposal(proposal)
                action = input("Approve (A) / Reject (R) / Skip (Enter): ").strip().upper()
                if action == "A":
                    try:
                        mandate = authority.approve_proposal(
                            proposal.proposal_id, approver_principal_id="console-human"
                        )
                        print(f"\nMandate issued: {mandate.mandate_id}")
                    except (KeyError, ValueError) as e:
                        print(f"Error: {e}")
                        return 1
                elif action == "R":
                    try:
                        authority.reject_proposal(
                            proposal.proposal_id, approver_principal_id="console-human"
                        )
                        print("Proposal REJECTED.")
                    except (KeyError, ValueError) as e:
                        print(f"Error: {e}")
                        continue
        elif choice == "2":
            active = authority.active()
            if not active:
                print("No active mandates.")
                continue
            for mandate in active:
                print("")
                _print_active(mandate)
        elif choice == "3":
            active = authority.active()
            if not active:
                print("No active mandates to revoke.")
                continue
            print("Active mandates:")
            for i, mandate in enumerate(active, start=1):
                print(f"{i}. {mandate.mandate_id} — {mandate.terms.purpose}")
            pick = input("Choose mandate to revoke (number): ").strip()
            try:
                chosen = active[int(pick) - 1]
            except (ValueError, IndexError):
                print("Invalid selection.")
                continue
            authority.revoke_mandate(chosen.mandate_id, revoker_principal_id="console-human")
            _print_revoked()
        elif choice == "4":
            print("Goodbye.")
            return 0
        else:
            print("Unknown choice.")


if __name__ == "__main__":
    raise SystemExit(main())
