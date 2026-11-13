# Low-Level Design — Bharat Connect MVP

## Package boundaries

- `domain/`: immutable values, payment states, errors and pure policy.
- `services/`: application workflows; orchestrate repositories and Cashfree client.
- `infrastructure/database.py`: PostgreSQL async pool lifecycle.
- `infrastructure/repositories.py`: SQL persistence implementations.
- `infrastructure/cashfree/client.py`: direct Cashfree COU HTTP client.
- `security/`: customer-parameter encryption and exact approval binding.
- `server.py`: FastMCP entry point and tool registration only.
- `audit.py`: structured audit event model and writer.

## Data model

- `billers`: cached BBPS biller metadata.
- `registered_accounts`: biller + HMAC fingerprint allowlist, encrypted parameters, masked label and owner confirmation.
- `fetched_bills`: immutable provider snapshot, integer paise, expiry and provider fetch reference.
- `payment_intents`: snapshot/account reference, amount, fee, state and local idempotency key.
- `payment_approvals`: exact snapshot/account/amount/fee binding, expiry and one-time consumption.
- `payment_attempts`: append-only provider submissions and outcomes.
- `audit_events`: actor/action/resource/correlation/outcome metadata.

See `migrations/0001_initial_schema.sql`.

## Important invariants

1. Intent amount must equal the immutable fetched-bill amount; model input cannot override it.
2. Intent account must equal the registered account attached to the fetched bill.
3. Biller and account fingerprint must both match the owner allowlist.
4. Approval is valid only for the exact intent, account, snapshot hash, amount, fee and expiry.
5. Approval consumption and transition to submitted are one transaction with row locking.
6. Unknown provider outcome blocks another payment attempt until reconciled.
7. All monetary values are integer paise.
8. Customer parameters are encrypted and masked outside the owner interface.

## Approval token

Canonical payload fields: intent ID, registered account ID, snapshot SHA-256, amount paise, fee paise and UTC expiry. The authenticated owner interface creates the approval after explicit review. Database state enforces single use; cryptographic validity alone is insufficient.

## Provider contract

No endpoint or request schema is assumed. Add typed Cashfree client methods only after the current COU partner API documentation is obtained. Confirm auth/signature, idempotency, timeout, status, reversal, fee, funding and reconciliation semantics first.

## Testing strategy

- Pure domain policy and amount-limit tests.
- Fernet encryption/decryption and key failure tests.
- Approval binding, tamper, expiry and replay tests.
- PostgreSQL integration tests for transactional approval consumption and uniqueness.
- Cashfree sandbox contract tests from official partner docs.
- MCP schema tests proving exactly five tools and no approval tool after implementation.
