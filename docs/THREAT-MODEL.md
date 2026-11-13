# Threat Model — Bharat Connect MCP

## Assets

Customer account parameters, bill details, payment approval, Cashfree credentials, provider references, audit history and the ability to initiate a financial transaction.

## Threat actors and failure modes

- Prompt injection or malicious biller/provider text attempts to steer the model.
- LLM hallucination or retry changes amount/account or duplicates payment.
- Unauthorized caller attempts to register an account or approve a payment.
- Stolen approval token is replayed or used for a modified payment.
- Database disclosure exposes account parameters.
- Provider timeout leaves payment outcome uncertain.
- Logs or MCP responses disclose customer identifiers or credentials.

## Controls

- The model cannot create or consume owner approval.
- Separate authenticated owner interface; exact approval binding and one-time transactional consumption.
- Amount and account loaded from immutable PostgreSQL records, not LLM arguments.
- Biller + account HMAC allowlist; encrypted customer parameters.
- Mask account details in model output and logs.
- Provider response treated as untrusted structured data.
- Unique local idempotency key and provider request references; unknown outcome requires reconciliation.
- PostgreSQL backups, least-privilege DB role, TLS, secret rotation and audit retention required before production.

## Residual risks / open gates

- Cashfree COU onboarding eligibility for a single-owner application is unconfirmed.
- Current partner API contract, funding model, idempotency and reconciliation semantics are not yet available.
- Account fetch/name match is not legal ownership proof.
- The authenticated owner UI and its identity/session protections are not implemented in this architecture milestone.
- Production use requires partner approval, security review and operational readiness.
