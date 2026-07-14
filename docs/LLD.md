# LLD — Payments Agent Gateway (MCP)

Low-level design, aligned to PLAN.md + HLD.md and the **verified** backend endpoints.

## 1. Repo layout
```
payments-mcp-server/
├── docs/{PLAN,HLD,LLD}.md
├── pyproject.toml                # mcp[cli], httpx, pydantic, pydantic-settings; dev: pytest, pytest-asyncio, respx, anthropic (evals only)
├── .env.example
├── src/payments_mcp/
│   ├── server.py                 # FastMCP: registers tools/resources/prompts
│   ├── config.py                 # settings + AgentPrincipal
│   ├── backend/
│   │   ├── base.py               # PaymentBackend protocol + DTOs
│   │   ├── http_backend.py       # HttpPaymentBackend (real Spring Boot API)
│   │   └── demo_backend.py       # DemoPaymentBackend (in-memory; dev + evals)
│   ├── policy.py                 # capability checks, risk/amount, approval
│   ├── operations.py             # operation_id -> idempotency-key (accept-or-derive)
│   ├── errors.py                 # backend ProblemDetail -> agent error taxonomy
│   ├── audit.py                  # audit record writer
│   └── tools.py                  # the 6 tools (thin: policy -> backend -> normalize -> audit)
├── evals/
│   ├── scenarios.yaml            # 15–20 scenarios
│   └── runner.py                 # drives an LLM through tools, scores metrics
└── tests/ (test_operations, test_policy, test_errors, test_tools)
```

## 2. Config + principal (`config.py`)
```python
class Settings(BaseSettings):
    backend: str = "demo"                      # "demo" | "http"
    backend_base_url: str = "http://localhost:8080"
    backend_token: str | None = None           # never exposed to the model
    request_timeout_s: float = 10.0

class AgentPrincipal(BaseModel):
    principal_id: str
    allowed_accounts: list[str]
    scopes: list[str]                          # payments:read|create|refund|admin
    max_payment_amount_minor: int = 10_000     # autonomous ceiling (₹100 demo)
```
Demo principal loaded from env/config; in real life it'd come from the MCP session/auth.

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
  - `get_balance`/`get_account_ledger`/`ledger_integrity` → **3 endpoints to add** to the Spring service (thin reads over `AccountBalanceRepository`, `LedgerEntryRepository`, `ReconciliationService`). Until added, these methods raise `NotImplemented` on http mode.
- **DemoPaymentBackend** — in-memory accounts/payments; enforces a *simplified* balance + idempotency replay so the full agentic flow (incl. retry/eval) works with **no local stack**.

## 4. Operation identity (`operations.py`) — the centerpiece
```python
def resolve_idempotency_key(principal_id: str, tool: str, args: dict,
                            operation_id: str | None) -> str:
    op = operation_id or _derive(principal_id, tool, args)   # accept-or-derive
    return "idem-" + hashlib.sha256(op.encode()).hexdigest()[:32], op

def _derive(principal_id, tool, args) -> str:
    norm = json.dumps({"p": principal_id, "t": tool, "a": _normalize(args)}, sort_keys=True)
    return "op-" + hashlib.sha256(norm.encode()).hexdigest()[:24]
```
- Same logical op (supplied or derived) → same backend key → backend replays → **exactly one side effect**.
- Same `operation_id` + **different** normalized payload → the gateway raises `IDEMPOTENCY_CONFLICT` *before* calling the backend.

## 5. Policy (`policy.py`)
```python
def authorize(principal, tool, args):
    if required_scope(tool) not in principal.scopes: raise Denied("PERMISSION_DENIED")
    for acct in accounts_in(args):
        if acct not in principal.allowed_accounts: raise Denied("ACCOUNT_NOT_ALLOWED")
    if tool == "create_payment" and args["amount_minor"] > principal.max_payment_amount_minor:
        raise ApprovalRequired()                 # -> APPROVAL_REQUIRED (not executed)
    rate_limit(principal.principal_id, tool)     # per-principal, per-tool
```
Input validation (amount > 0 and ≤ ceiling, currency in allow-list, ids well-formed) happens before authorize.

## 6. Error normalization (`errors.py`)
Map backend HTTP/ProblemDetail → the taxonomy in HLD §7. Always return:
```json
{"code":"INSUFFICIENT_FUNDS","message":"...","retryable":false,
 "suggested_action":"<gateway-owned, deterministic>","correlation_id":"corr_..."}
```
`suggested_action` comes from a fixed `CODE -> action` map in the gateway — never from backend text (prompt-injection guard). Never leak stack traces.

## 7. Audit (`audit.py`)
Append a record (HLD §8 fields) per tool call: `timestamp, correlation_id, principal_id, tool, operation_id, arguments_hash, policy_decision, backend_request_id, backend_resource_id, result_code, latency_ms`. Log to stdout/JSONL for the demo; no raw sensitive args (hash them).

## 8. Tools (`tools.py` + `server.py`) — thin orchestration
Every tool follows the same shape: **validate → authorize → resolve op key → call backend → normalize → audit → return.** FastMCP derives each tool's schema from type hints + docstring, so docstrings are part of the contract (the model reads them).

| Tool | Scope | Backend | Notes |
|---|---|---|---|
| `get_payment(payment_id)` | read | ✅ `GET /v1/payments/{id}` | readOnlyHint |
| `get_balance(account_id)` | read | ⚠️ new endpoint / demo | readOnlyHint, bounded |
| `get_account_ledger(account_id, cursor?, limit=20)` | read | ⚠️ new endpoint / demo | readOnlyHint, **bounded page** (never dump full history) |
| `create_payment(payer, payee, amount_minor, currency="INR", operation_id?)` | create | ✅ `POST /v1/payments` | idempotent via op key; amount ceiling → APPROVAL_REQUIRED |
| `refund_payment(payment_id, amount_minor?, operation_id?)` | refund | ✅ refunds endpoint | destructiveHint; more consequential than create |
| `check_ledger_integrity()` | admin | ⚠️ new endpoint / demo | admin scope only; returns `{balanced, ledger_net, drifted_accounts}` |

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
