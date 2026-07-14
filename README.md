# Payments Agent Gateway (MCP)

![Python](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-FastMCP-6E56CF)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)
![Tests](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)

An **MCP server** that safely exposes a correctness-critical, double-entry **payments ledger** to a
probabilistic **AI agent**.

> **Thesis:** *How do you let an LLM agent operate a money-moving system without it double-charging,
> exceeding its authority, or being steered by prompt injection?* The gateway answers that — the
> **model generates intent; the gateway enforces the boundary; the ledger stays the source of truth.**

Sits on top of my [payments-wallet-service](https://github.com/saikrishnamulukutla/payments-wallet-service)
(Java/Spring Boot: immutable double-entry ledger, idempotency, saga, transactional outbox).

---

## Why it's interesting
The backend already solves *deterministic* correctness. The new problem is a **non-deterministic caller**:

- **The model will retry.** A lost response must not create a second payment → the gateway maps a
  **logical `operation_id` → a stable backend idempotency key**, so a retry replays instead of
  double-charging. *(The centerpiece — connects distributed-systems work to agentic engineering.)*
- **The model can be injected / hallucinate.** Every mutating request passes **capability policy**
  (scopes + account allow-list + amount ceiling) *before* the backend is touched — the OWASP
  **structured-invocation** control: “the model proposes; the validator disposes.”
- **Some actions are irreversible.** Amounts over the agent's autonomous limit return
  **`APPROVAL_REQUIRED`** (human-in-the-loop) — a model saying “I confirm” is not security.

## Architecture
```
User ──NL──▶ LLM/Agent ──MCP(stdio/http)──▶  Payments MCP Gateway (Python/FastMCP)
                                             • tool schemas + validation
                                             • capability policy (AgentPrincipal + scopes)
                                             • operation_id → idempotency key
                                             • agent error taxonomy + audit
                                                     │ PaymentBackend (protocol)
                                       ┌─────────────┴─────────────┐
                                       ▼                           ▼
                              HttpPaymentBackend            DemoPaymentBackend
                              (real Spring Boot API)        (in-memory; dev + evals)
                                       │
                                       ▼   PostgreSQL · Redis · Kafka
                              double-entry · idempotency · saga · outbox · reconciliation
```
**Rule:** the gateway constrains what an agent may *request*; the ledger determines what is
financially *valid* and executes it atomically. The gateway never re-implements balances,
idempotency, or postings.

## Safety model (maps to OWASP MCP controls)
| Control | Here |
|---|---|
| Structured invocation | `policy.py` validates scope + account + amount before any backend call |
| Human-in-the-loop | `APPROVAL_REQUIRED` for amounts over the principal's ceiling |
| Context compartmentalization | least-privilege `AgentPrincipal` (scopes + account allow-list); creds never seen by the model |
| Model-readable failures | stable error taxonomy: `{code, message, retryable, retry_after_seconds, suggested_action, correlation_id}` — `suggested_action` is gateway-owned (not echoed from backend data) |
| Auditability | per-call record correlating agent intent → backend execution |

## Tools
| Tool | Scope | Annotation |
|---|---|---|
| `get_payment` / `get_balance` / `get_account_ledger` | `payments:read` | read-only (ledger bounded) |
| `create_payment` | `payments:create` | idempotent |
| `refund_payment` | `payments:refund` | destructive |
| `check_ledger_integrity` | `payments:admin` | read-only, admin |

Plus resources `payments://capabilities`, `payments://payment/{id}` and prompt `explain_payment`.

## Quickstart
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# run it under the MCP Inspector (interactive)
mcp dev src/payments_mcp/server.py

# or register with Claude Desktop
mcp install src/payments_mcp/server.py

# tests
pytest -q
```
Defaults to `PAYMENTS_BACKEND=demo` (in-memory, no external services). Set `PAYMENTS_BACKEND=http`
+ `PAYMENTS_BASE_URL` to drive the real Spring Boot service.

## Demo (the money shot)
Ask the agent: **“Pay 50 from acct-A to acct-B, then retry the exact same operation.”** →
one payment, one debit. Then **“pay 500000 from acct-A to acct-B”** → `APPROVAL_REQUIRED`.
Then **“pay from acct-Z”** → `ACCOUNT_NOT_ALLOWED`. The audit log shows intent → idempotency key →
payment → (in http mode) ledger postings.

## Evals
`python -m evals.runner` drives an LLM through behavior scenarios (normal / risk / authorization /
hallucination / retry / adversarial) and asserts **duplicate-financial-operation count = 0**. Needs
`ANTHROPIC_API_KEY` in `.env`; without it, the *deterministic* exactly-once proof still runs via
`pytest tests/test_m3_create.py`.

## Layout
```
src/payments_mcp/  server.py · gateway.py · policy.py · operations.py · errors.py · audit.py · config.py · backend/{base,http_backend,demo_backend}.py
evals/             scenarios.py · runner.py
tests/             backend · policy · create (the centerpiece) · refund/admin
scripts/           smoke_m0..m5.py (end-to-end via a real MCP client)
docs/              PLAN · HLD · LLD
```

## Non-goals
No second payments backend · no MCP-side financial logic · no autonomous reconciliation/repair ·
no generic SQL/`call_api` tool · no model-generated authorization · no fake approval · no
vector-DB-for-RAG-decoration. The project stays a *boundary* between an agent and a correct ledger.
