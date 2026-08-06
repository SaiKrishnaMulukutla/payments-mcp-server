# LLD — Payments Agent Gateway (MCP)

Low-level design, aligned to PLAN.md + HLD.md and the **verified** backend endpoints.

## 1. Repo layout
```
payments-mcp-server/
├── docs/{PLAN,HLD,LLD}.md
├── pyproject.toml                # mcp[cli], httpx, pydantic, pydantic-settings; extras: dev, redis, auth, evals
├── .env.example
├── src/payments_mcp/
│   ├── server.py                 # FastMCP: registers tools/resources/prompts; builds gateway + verifiers
│   ├── gateway.py                # trust boundary: validate→authorize→resolve→execute→audit
│   ├── config.py                 # Settings + AgentPrincipal
│   ├── backend/{base,http_backend,demo_backend}.py   # PaymentBackend protocol + DTOs; real + in-memory
│   ├── policy.py                 # capability checks, amount/currency, approval
│   ├── operations.py             # operation_id -> idempotency-key (accept-or-derive)
│   ├── opstore.py                # OperationStore: in-memory | Redis (H2)
│   ├── identity.py               # OAuth2.1 token verify + MCP adapter -> per-request principal (H3)
│   ├── mandate.py                # signed payment-mandate verify/enforce (H4)
│   ├── issuer.py                 # approval -> signed mandate (human-in-the-loop)
│   ├── errors.py                 # backend ProblemDetail -> agent error taxonomy
│   └── audit.py                  # audit record writer (stderr)
├── evals/{scenarios.py, runner.py}
├── .github/workflows/ci.yml      # lint (ruff) + type (mypy) + test (pytest)
├── Dockerfile
└── tests/ (test_m1_backend, test_m2_policy, test_m3_create, test_m4_m5, test_h1_http_errors, test_h2_opstore, test_h3_identity, test_h4_mandate, test_a2_auth, test_a3_issuer)
```

## 2. Config + principal (`config.py`)
```python
class Settings(BaseSettings):                  # env prefix PAYMENTS_
    backend: str = "demo"                      # "demo" | "http"
    base_url: str = "http://localhost:8080"
    token: str | None = None                   # backend bearer; never exposed to the model
    request_timeout_s: float = 10.0
    merchant_id: str = "mcp-agent"
    redis_url: str | None = None               # H2: shared op store + lock; unset => in-memory
    auth_secret / auth_issuer / auth_audience  # H3: bearer-token verification (unset => no auth)
    auth_resource_url: str | None = None       # H3: this server's URL (RFC 8707 resource indicator)
    mandate_secret / mandate_issuer            # H4: mandate verification (unset => mandates off)
    transport: str = "stdio"                   # stdio | streamable-http
    host: str = "127.0.0.1"; port: int = 8000  # streamable-http bind

class AgentPrincipal(BaseModel):
    principal_id: str
    allowed_accounts: list[str]
    scopes: list[str]                          # payments:read|create|refund|admin
    max_payment_amount_minor: int = 10_000     # autonomous ceiling (₹100 demo)
```
Demo principal (stdio path) from `demo_principal()`; the HTTP path resolves the principal per request
from a verified token via `identity.resolve_principal` (H3).

## 3. Backend abstraction (`backend/`)
The gateway talks to a **protocol**, never raw httpx — so tools are decoupled and evals run against the demo.
```python
class PaymentBackend(Protocol):
    async def create_payment(self, *, idempotency_key: str, payer: str, payee: str,
                             amount_minor: int, currency: str) -> Payment: ...
    async def get_payment(self, payment_id: str) -> Payment: ...
    async def refund_payment(self, *, payment_id: str, amount_minor: int | None,
                             idempotency_key: str) -> Refund: ...
    async def get_balance(self, account_id: str) -> Balance: ...          # needs new endpoint
    async def get_account_ledger(self, account_id: str, cursor: str | None,
                                 limit: int) -> LedgerPage: ...            # needs new endpoint
    async def ledger_integrity(self) -> IntegrityReport: ...              # needs new endpoint
```
- **HttpPaymentBackend** — maps to the **verified** endpoints:
  - `create_payment` → `POST /v1/payments` with header `Idempotency-Key: <key>`
  - `get_payment` → `GET /v1/payments/{id}`
  - `refund_payment` → `POST /v1/payments/{paymentId}/refunds` (Idempotency-Key)
  - `get_balance` → `GET /v1/accounts/{id}/balance`, `get_account_ledger` → `GET /v1/accounts/{id}/ledger?limit`, `ledger_integrity` → `GET /v1/reconciliation/report` — all added to the Spring service and mapped. HTTP failures normalize to the taxonomy (see §6; 409 → `OPERATION_IN_PROGRESS`, 422 body-mismatch → `IDEMPOTENCY_CONFLICT`).
- **DemoPaymentBackend** — in-memory accounts/payments; enforces a *simplified* balance + idempotency replay so the full agentic flow (incl. retry/eval) works with **no local stack**.

## 4. Operation identity (`operations.py`) — the centerpiece
```python
class Operations:                                            # store: InMemory | Redis (opstore.py)
    async def resolve(self, principal_id, tool, args, operation_id) -> (idempotency_key, op):
        op = operation_id or _derive(principal_id, tool, args)   # accept-or-derive
        await self._store.reserve(op, args_hash(...))            # conflict + fail-fast lock (H2)
        return "idem-" + sha256(op)[:32], op
    async def complete(self, op): await self._store.release(op)  # release lock after backend call
```
- Same logical op (supplied or derived) → same backend key → backend replays → **exactly one side effect**.
- Same `operation_id` + **different** normalized payload → `IDEMPOTENCY_CONFLICT` *before* the backend.
- Concurrent duplicate (lock held) → `OPERATION_IN_PROGRESS`. The `OperationStore` is Redis-backed in prod (`opstore.py`, H2), in-memory otherwise; the backend's unique constraint remains the real arbiter.
- A signed **mandate** (H4), when supplied, sets `operation_id = mandate_id` so identity is anchored to a business artifact, not model-sent args.

## 5. Policy (`policy.py`)
```python
# policy.py — each check raises BackendError(<code>) so failures normalize like backend errors
require_scope(principal, scope)                 # -> PERMISSION_DENIED
require_account(principal, account_id)          # -> ACCOUNT_NOT_ALLOWED
validate_amount(amount); validate_currency(cur) # -> INVALID_ARGUMENT
require_within_limit(principal, amount)         # over ceiling -> APPROVAL_REQUIRED (create AND refund)
```
Validation (amount > 0, currency in allow-list) runs before the scope/account/limit checks. Per-principal
rate limiting is deferred (not yet implemented).

## 6. Error normalization (`errors.py`)
Map backend HTTP/ProblemDetail → the taxonomy in HLD §7. Always return:
```json
{"code":"INSUFFICIENT_FUNDS","message":"...","retryable":false,
 "suggested_action":"<gateway-owned, deterministic>","correlation_id":"corr_..."}
```
`suggested_action` comes from a fixed `CODE -> action` map in the gateway — never from backend text (prompt-injection guard). Never leak stack traces.

## 7. Audit (`audit.py`)
Append a record (HLD §8 fields) per tool call: `timestamp, correlation_id, principal_id, tool, operation_id, arguments_hash, policy_decision, backend_resource_id, result_code, latency_ms`. Log as JSON to **stderr** (stdout carries the MCP protocol, so it must stay clean); no raw sensitive args (hash them).

## 8. Tools (`tools.py` + `server.py`) — thin orchestration
Every tool follows the same shape: **validate → authorize → resolve op key → call backend → normalize → audit → return.** FastMCP derives each tool's schema from type hints + docstring, so docstrings are part of the contract (the model reads them).

| Tool | Scope | Backend | Notes |
|---|---|---|---|
| `get_payment(payment_id)` | read | ✅ `GET /v1/payments/{id}` | readOnlyHint |
| `get_balance(account_id)` | read | ✅ `GET /v1/accounts/{id}/balance` | readOnlyHint, bounded |
| `get_account_ledger(account_id, limit=20)` | read | ✅ `GET /v1/accounts/{id}/ledger?limit` | readOnlyHint, **bounded page** (never dump full history) |
| `create_payment(payer, payee, amount_minor, currency="INR", operation_id?, mandate?)` | create | ✅ `POST /v1/payments` | idempotent via op key; ceiling → APPROVAL_REQUIRED; signed `mandate` (H4) |
| `refund_payment(payment_id, amount_minor, operation_id?, mandate?)` | refund | ✅ refunds endpoint | destructiveHint; more consequential than create |
| `check_ledger_integrity()` | admin | ✅ `GET /v1/reconciliation/report` | admin scope only; returns `{balanced, ledger_net, drifted_accounts}` |

Annotations: reads `readOnlyHint:true`; `create_payment` `idempotentHint:true`; `refund_payment` `destructiveHint:true`.

## 9. Resources & prompt
- `@mcp.resource("payments://capabilities")` → renders the principal's accounts/scopes/limit.
- `@mcp.resource("payments://payment/{id}")` → bounded payment context.
- `@mcp.prompt() explain_payment(payment_id)` → template: explain lifecycle from status+ledger, no invented facts.

## 10. Evals (`evals/`)
`scenarios.yaml` = the 15–20 cases (HLD §11). `runner.py` drives an LLM (Anthropic SDK) with the tools against `DemoPaymentBackend`, asserts expected behavior, and computes metrics; **duplicate-financial-operation count must be 0**. Flagship = the retry scenario. (This milestone needs `ANTHROPIC_API_KEY`.)

## 11. Milestones → files (aligned to PLAN M0–M7; ⭐ = minimum-lovable stop)
- **M0** — `server.py` + `ping`; run via `mcp dev`; connect Claude Desktop. *(no architecture yet)*
- **M1** — `backend/{base,http_backend,demo_backend}.py`, `config.py`, DTOs, timeouts, `errors.py` skeleton, correlation ids. *(MCP decoupled from httpx)*
- **M2** — read tools `get_payment` (real) + `get_balance`/`get_account_ledger` (demo or after adding endpoints); `AgentPrincipal` + read scope + account policy + bounded responses.
- **M3 ⭐** — `create_payment` + `operations.py` (accept-or-derive) + `policy.py` (scope/account/ceiling) + `errors.py` + `audit.py`. Tests: same op replay; op+different payload → conflict; commit+lost-response+retry → exactly one; unauthorized account → no mutation; insufficient funds → non-retryable error. **The core milestone.**
- **M4** — `refund_payment` + refund scope + risk classification + `APPROVAL_REQUIRED` (no demo approval service).
- **M5** — resources (`capabilities`, `payment/{id}`) + `explain_payment` prompt + `check_ledger_integrity` admin tool; prove non-admins are denied.
- **M6** — `evals/` suite + metrics (needs API key).
- **M7** — README + threat model + demo script + Inspector screenshots; optional streamable-HTTP.

**Recommended ship order (job-hunt pragmatic):** M0 → M1 → M2 → **M3** → a 5–6-scenario slice of M6 (incl. retry) → README. Then M4/M5/M7 if runway remains.

## 12. Optional backend addition (unlocks 3 tools on the real path)
In payments-wallet-service, add thin read controllers (data already exists):
- `GET /v1/accounts/{id}/balance` → `AccountBalanceRepository`
- `GET /v1/accounts/{id}/ledger?cursor&limit` → `LedgerEntryRepository` (paged)
- `GET /v1/reconciliation/report` (admin) → `ReconciliationService.report()`
~1 hour; also gives you more real backend work to discuss. If skipped, those 3 tools use `DemoPaymentBackend`.

## 13. Commands
```bash
uv venv && source .venv/bin/activate
uv pip install "mcp[cli]" httpx pydantic pydantic-settings
mcp dev src/payments_mcp/server.py     # MCP Inspector
mcp install src/payments_mcp/server.py # register with Claude Desktop
pytest
python -m evals.runner                 # needs ANTHROPIC_API_KEY (M6)
```
