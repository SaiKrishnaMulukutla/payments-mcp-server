# HLD — Payments Agent Gateway (MCP)

High-level design, aligned to PLAN.md. **Thesis:** *how do you safely expose a deterministic, correctness-critical payments system to a probabilistic AI agent?*

## 1. Architecture

```
        User (natural language)
                │
                ▼
        LLM / Agent  ── reasons, chooses a tool + args ──┐
                │  MCP (JSON-RPC over stdio / HTTP)        │
                ▼                                          │
 ┌───────────────────────────────────────────────┐        │
 │        Payments MCP Agent Gateway (Python)      │        │
 │  tool schemas · input validation                │        │
 │  capability policy (AgentPrincipal + scopes)     │  results / structured errors
 │  risk classification · approval boundary         │────────┘
 │  operation_id → idempotency-key mapping          │
 │  agent-oriented error taxonomy · audit records   │
 └───────────────────────┬─────────────────────────┘
                         │ PaymentBackend (protocol)
             ┌───────────┴───────────┐
             ▼                       ▼
   HttpPaymentBackend         DemoPaymentBackend
   (real Spring Boot API)     (in-memory, for dev + evals)
             │
             ▼
   Existing Spring Boot Payments API ──► PostgreSQL · Redis · Kafka
   (idempotency, double-entry, saga, refunds, reconciliation, outbox)
```

**The trust boundary is the gateway.** The client owns the model; the gateway owns *what an agent may request* and the safety around it; the ledger owns *what is financially valid* and executes it atomically. The model never sees credentials — only tool schemas.

> **Rule (put in README):** the gateway constrains what an agent may *request*; the ledger determines what is financially *valid* and executes it.

## 2. Backend endpoint map (verified against payments-wallet-service)
| Gateway tool | Backend endpoint | Status |
|---|---|---|
| `create_payment` | `POST /v1/payments` (`Idempotency-Key` header) | ✅ exists |
| `get_payment` | `GET /v1/payments/{id}` | ✅ exists |
| `refund_payment` | `POST /v1/payments/{paymentId}/refunds` | ✅ exists |
| `get_balance` | `GET /v1/accounts/{id}/balance` | ✅ added (`AccountController`) |
| `get_account_ledger` | `GET /v1/accounts/{id}/ledger?limit` | ✅ added (`AccountController`, bounded) |
| `check_ledger_integrity` | `GET /v1/reconciliation/report` | ✅ added (`ReconciliationController`) |

All six tools now map to real endpoints. `DemoPaymentBackend` remains for dev + evals (build/test the MCP layer with no local stack running).

## 3. The model-decides / gateway-executes loop
```
"Refund payment pay_123 for ₹500"
 1. Agent reads tool schemas
 2. Agent calls refund_payment(payment_id, amount, operation_id)
 3. Gateway: validate args → check principal scope + account access + risk/amount policy
 4. Gateway: map operation_id → stable idempotency key → call backend
 5. Gateway: normalize result/error → structured, model-readable response
 6. Gateway: write an audit record (intent → execution)
 7. Agent reasons over the result
```
Steps 3–6 are the project — the gateway is where correctness-of-*access* lives (correctness-of-*money* stays in the ledger).

## 4. Capability model (real least-privilege)
Not "one unrestricted credential." A principal is scoped:
```
AgentPrincipal { principal_id, allowed_accounts[], scopes[], max_payment_amount_minor }
```
| Capability | payments:read | payments:create | payments:refund | payments:admin |
|---|:-:|:-:|:-:|:-:|
| get_balance / get_payment / get_account_ledger | ✓ | | | |
| create_payment | | ✓ | | |
| refund_payment | | | ✓ | |
| check_ledger_integrity | | | | ✓ |
Keeping creds from the model is necessary but insufficient — if the gateway holds an unrestricted credential and exposes every tool, the model effectively has every capability. The scoped principal fixes that.

## 5. The centerpiece — logical-operation identity across a probabilistic caller
The backend's idempotency only protects retries carrying the **same key**. A flaky agent may retry and, without care, generate a *new* key → a second payment. The gateway maintains **logical-operation identity**:
```
agent operation_id ──(deterministic)──► backend Idempotency-Key ──► PostgreSQL arbitration ──► exactly one side effect
```
**Rule (sharpened):** the gateway **accepts an `operation_id` if supplied; otherwise derives one deterministically from `(principal_id, tool, normalized_args)`.** Either way, a retry of the *same logical operation* reuses the *same* backend key. Same `operation_id` + *different* payload → `IDEMPOTENCY_CONFLICT` (reject, never silently create). This is where the backend work and the agentic work genuinely meet.

## 6. Risk & approval (no demo approval)
Read → execute if authorized. Write → check scope, account access, amount ceiling; if it exceeds the principal's autonomous limit, return `APPROVAL_REQUIRED` (not executed). A model saying "I confirm" is **not** security — real approval must come from an external/client-side channel (deferred to M4). Refunds are marked more consequential than payments.

## 7. Agent-oriented error taxonomy
Backend returns RFC-7807 `ProblemDetail`; the gateway **normalizes** to a stable, model-readable taxonomy: `INVALID_ARGUMENT, PERMISSION_DENIED, ACCOUNT_NOT_ALLOWED, PAYMENT_NOT_FOUND, INSUFFICIENT_FUNDS, IDEMPOTENCY_CONFLICT, OPERATION_IN_PROGRESS, APPROVAL_REQUIRED, RATE_LIMITED, BACKEND_TIMEOUT, BACKEND_UNAVAILABLE, INTERNAL_ERROR`. Each failure carries `retryable`, an optional gateway-owned `suggested_action`, and a `correlation_id`. **`suggested_action` is chosen from deterministic gateway mappings — never echoed from backend data** (prompt-injection guard).

## 8. Auditability (intent → execution)
The ledger/outbox already record the financial truth; the gateway adds *"what did the agent request?"*:
```
timestamp, correlation_id, principal_id, tool_name, operation_id,
arguments_hash, policy_decision, backend_request_id, backend_resource_id, result_code, latency_ms
```
→ one operation is traceable: NL intent → MCP call (corr-id, op-id) → policy decision → `POST /v1/payments` (idem key) → payment id → ledger postings → outbox event.

## 9. Resources & prompts (implement, don't overinvest — tools are the heart)
- Resource `payments://capabilities` → what *this* principal may do (accounts, limits, scopes).
- Resource `payments://payment/{id}` → bounded payment context.
- Prompt `explain_payment` → explain a payment's lifecycle from status + ledger movement, without inventing facts absent from the backend.

## 10. Transports
- **stdio** (M0–M6): gateway is a subprocess of the client (Claude Desktop/Code, MCP Inspector). Simplest.
- **streamable HTTP** (M7, optional): remote transport — only if genuinely useful, not to tick a box.

## 11. Evals (what makes this agentic-reliability, not an adapter)
A harness drives an LLM through 15–20 scenarios (normal / ambiguous / retry / adversarial / authorization / risk / failure / idempotency / hallucination / loop) and measures: task-success, **unsafe-action rate**, unnecessary-tool-call rate, clarification rate, and **duplicate-financial-operation count (must be 0)**. The retry scenario (backend commits → response lost → agent retries same `operation_id` → exactly one payment) is the flagship test. *Note: evals need an LLM key + a small agent runner — the only part that isn't keyless.*

## 12. Trade-offs / conscious decisions
- **Adapter, not a second backend** — the gateway never re-implements balance/idempotency/postings; those stay in the ledger.
- **Full Spring service target** (not the live Edge `/api/pay`) — richer tools, but demoing the *real* path needs the local stack; `DemoPaymentBackend` covers dev/evals without it.
- **operation_id accept-or-derive** — documented because it's the exact thing an interviewer probes.
- **stdio first** — fastest to a working demo; HTTP is additive.
