# Bharat Connect MCP Server

A single-owner MCP application for BBPS bill discovery, bill fetch, registered-account management and explicitly approved bill payments.

> **Safety boundary:** the model may discover and prepare a payment, but it cannot authorize one. Payment approval is performed through a separate authenticated owner interface and is bound to the exact bill snapshot, account, amount, fees and expiry.

## MVP scope

- One owner; onboarding is intentionally configured out-of-band for the MVP.
- PostgreSQL is the durable source of truth.
- FastMCP exposes exactly five tools once their service implementations are complete:
  1. `search_billers`
  2. `fetch_bill`
  3. `check_wallet_balance`
  4. `pay_bill`
  5. `get_payment_status`
- Cashfree BBPS COU is the intended integration. No endpoint paths or payloads are assumed until Cashfree provides the current partner API contract.
- Every payment requires a fresh, independent owner approval. No autonomous or recurring payments.
- Maximum single bill defaults to ₹10,000 (1,000,000 paise), configurable by the owner.
- Registered account allowlist is enforced server-side.

**This branch establishes the architecture and database foundation. The five payment tools are deliberately not exposed yet.** They will be added with their service implementations and tests, after the Cashfree COU contract and onboarding are confirmed.

## Payment flow

1. Search locally cached biller metadata.
2. Fetch a bill from BBPS and persist an immutable snapshot.
3. Verify that the biller + customer-account combination is registered.
4. Create a payment intent from the stored snapshot; never accept an LLM-supplied amount.
5. Owner reviews the exact bill in a separate authenticated interface.
6. Approval is single-use, short-lived and cryptographically bound to account, snapshot hash, amount and fees.
7. Execute through Cashfree using documented provider semantics.
8. Persist provider references and reconcile pending/unknown outcomes. Never blindly retry an uncertain payment.

## Account registration

The owner enters biller-specific customer parameters in the authenticated management interface. The application fetches the account through BBPS and displays the biller-returned customer details (when available). The owner independently confirms registration.

A successful fetch means the biller accepted the supplied lookup details; it does **not** prove legal ownership or KYC. Customer parameters are encrypted at rest. An HMAC fingerprint supports exact account matching without exposing raw identifiers to the model or logs.

## Architecture

```
MCP client / LLM
      |
  FastMCP (five narrow tools; no approval tool)
      |
  Application services ---- Owner approval/account UI (separate auth boundary)
      |                                  |
      +----------- PostgreSQL -----------+
      |
  Cashfree BBPS COU client (direct, contract-driven)
      |
  BBPS network
```

Modular monolith; no Redis, no generic provider framework, no BBPS double-entry ledger. The existing Java/Spring Boot wallet service and its double-entry ledger are a separate project and are not modified here.

## Project layout

```
src/payments_mcp/
  server.py
  config.py
  domain/                 # domain models, errors and deterministic policy
  services/               # biller, account, bill and payment use cases
  infrastructure/
    database.py
    repositories.py
    cashfree/client.py    # direct Cashfree COU integration boundary
  security/                # account encryption and approval binding
  audit.py
migrations/
docs/                      # plan, HLD, LLD and threat model
tests/
```

## Local development

Python 3.11+ and PostgreSQL 15+ are expected.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Configure the required secrets and PostgreSQL URL before starting services.
pytest -q
ruff check src tests
```

The server currently starts as an intentionally empty FastMCP shell: it does not initiate payments or expose placeholder tools. Database migrations are applied explicitly; the application never silently falls back to SQLite, in-memory storage, demo identities or a fake payment provider.

## Readiness gates before live payments

- Written confirmation from Cashfree that this single-owner application is eligible to onboard as an Agent Institution / COU customer.
- Current Cashfree COU API contract, credentials, funding/settlement model, available-balance semantics, idempotency rules and status/reconciliation requirements.
- Sandbox integration tests and any required certification.
- Independent authenticated owner UI, approval consumption, audit review and operational reconciliation.
- Security review before production credentials are enabled.
