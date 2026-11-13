# MVP Plan — Bharat Connect MCP Server

## Product boundary

A private, single-owner bill-payment assistant using Bharat Connect (BBPS) through a Cashfree COU relationship. This is not a public fintech, wallet, payment aggregator or multi-tenant product.

## Decisions (approved)

- One owner; hardcoded onboarding for MVP.
- PostgreSQL only; no SQLite and no Redis.
- FastMCP; modular monolith.
- Five MCP tools only: `search_billers`, `fetch_bill`, `check_wallet_balance`, `pay_bill`, `get_payment_status`.
- Direct Cashfree COU client; no generic payment-provider abstraction.
- No BBPS double-entry ledger and no reusable mandate framework.
- No changes to the separate Java/Spring Boot wallet service or its ledger.
- Single bill cap defaults to ₹10,000.
- Every payment requires independent owner approval; no auto-pay.
- Provider facts and money amounts are server-owned, not LLM-supplied.

## Milestones

### M0 — architecture reset (this commit)
- Replace wallet-transfer gateway design with BBPS domain structure.
- Establish PostgreSQL schema, security primitives and threat model.
- Remove obsolete demo backend, Redis operation store, generic mandate and wallet-transfer tools.
- Do not expose nonfunctional MCP tools.

### M1 — Cashfree onboarding and contract
- Obtain written eligibility confirmation for a single-owner application.
- Obtain current COU API docs, sandbox credentials and operational requirements.
- Confirm biller metadata, fetch, payment, status, reconciliation, balance/funding, fees, idempotency and timeout semantics.
- No guessed endpoints.

### M2 — persistence and biller/account workflows
- Implement migrations and PostgreSQL repositories.
- Sync/cache biller metadata.
- Implement account registration: fetch → show returned name/details → owner confirms → encrypt params and store HMAC fingerprint.
- Add tests for exact account allowlisting and data redaction.

### M3 — bill fetch and payment intent
- Implement fetch and immutable snapshot storage.
- Create intent using only stored fetch reference; derive amount from persisted provider snapshot.
- Reject expired, changed, unregistered or over-limit bills.
- Add snapshot-hash and intent-binding tests.

### M4 — independent approval
- Build separate authenticated owner interface.
- Approval binds exact intent, account, snapshot hash, amount, fees and expiry.
- Consume once transactionally; never expose approval creation/consumption as an LLM-callable MCP tool.
- Test replay, expiry, altered amount, altered account and concurrent consumption.

### M5 — payment execution and status
- Execute through Cashfree using documented idempotency/request-reference semantics.
- Unknown/pending outcomes must reconcile; never blindly retry.
- Persist provider refs, attempts, status transitions and audit.
- Implement status reconciliation and failure recovery.

### M6 — five MCP tools and security hardening
- Expose exactly the five approved tools, no generic HTTP/SQL tools.
- Keep tool outputs bounded and redact account identifiers.
- Verify that model-controlled arguments cannot alter amounts or bypass approval.
- Add end-to-end sandbox tests and CI.

### M7 — production readiness
- Cashfree/NBBL onboarding and any certification complete.
- Threat model, secrets rotation, backups, monitoring, incident/runbook, reconciliation and dispute process reviewed.
- Production credentials remain disabled until all gates pass.

## Exit criteria

No real-money payment until Cashfree confirms eligibility and the exact COU contract is implemented and tested. A tool call alone is never owner approval.
