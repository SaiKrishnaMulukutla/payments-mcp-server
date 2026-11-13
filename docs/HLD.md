# High-Level Design — Bharat Connect MCP

## 1. Scope

Private, single-owner application. It offers biller discovery and bill payment through an eligible BBPS COU partner. It does not operate as a COU/BOU, PA, wallet, ledger or public multi-tenant service.

## 2. Components

- **MCP interface:** FastMCP, eventually exactly five narrow tools. No approval tool.
- **Application services:** biller search, account registration, bill fetch, payment intent, payment execution and status reconciliation.
- **Owner interface:** separate authenticated surface for account confirmation and exact payment approval.
- **PostgreSQL:** durable source of truth for metadata, registered accounts, immutable bill snapshots, intents, approvals, attempts and audit.
- **Cashfree client:** direct adapter built only from current Cashfree COU partner documentation.
- **Security:** Fernet encryption for biller parameters; HMAC fingerprint for account matching; approval signature plus transactional single-use consumption.

## 3. Payment lifecycle

```
search biller -> fetch bill -> persist immutable snapshot
                            -> registered biller/account allowlist
                            -> create intent from stored snapshot
                            -> owner reviews exact amount + fees
                            -> authenticated one-time approval
                            -> Cashfree payment submission
                            -> provider status + reconciliation
```

A provider timeout after submission is an unknown outcome, not a failed payment. Reconcile using documented status/reference APIs before any retry.

## 4. Trust boundaries

- The LLM is an untrusted caller and cannot set trusted bill amount, customer identity, provider reference or approval state.
- `pay_bill` will accept a stored fetch/intent reference only. The server loads the amount and account from PostgreSQL.
- Approval is out-of-band and exact-binding. A boolean argument or model-generated statement is not authorization.
- Provider responses are untrusted data. They are schema-validated and never treated as instructions.
- Account customer parameters are encrypted; raw values never appear in tool output or logs.
- Successful fetch validates biller lookup inputs, not legal ownership or KYC.

## 5. Persistence and transaction boundaries

- PostgreSQL is mandatory. No in-memory or SQLite fallback.
- Approval consumption, payment-attempt creation and intent transition must be atomic under row locks.
- Unique constraints protect provider fetch references, local idempotency keys, approval tokens and attempt numbers.
- Every state transition writes an audit event in the same transaction where feasible.
- Never delete or overwrite the immutable fetched bill snapshot.

## 6. Money and limits

- Store INR in integer paise; never use binary floating point.
- Maximum single bill defaults to 1,000,000 paise (₹10,000).
- Fees are separately stored and included in the approval binding.
- No autonomous payments, recurring mandates or reusable approvals in MVP.

## 7. Operational safety

- Sandbox by default; production configuration disabled until partner onboarding and certification.
- Provider idempotency is implemented only if documented.
- Pending/unknown payments are reconciled, not blindly retried.
- Cashfree wallet/payout balance is not assumed to be BBPS funding balance. Funding and settlement model must be confirmed.
- No secrets in MCP schemas, logs, exceptions or model-visible output.

## 8. Explicit non-goals

No Redis, microservices, generic provider framework, BBPS double-entry ledger, generic SQL/HTTP MCP tool, auto-pay, reusable mandate engine or changes to the separate wallet service.
