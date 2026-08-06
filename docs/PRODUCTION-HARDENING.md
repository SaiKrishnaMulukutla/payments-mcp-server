# Production-Hardening Plan

Turns the current portfolio-grade gateway into a production-grade one, based on the Aug 2026
research into MCP auth, Stripe idempotency, agentic-payment standards (AP2/ACP), and Redis
race-condition patterns. See the end for sources.

## Guiding principle (unchanged)

> **The gateway validates identity, authorizes intent, and makes retries safe. The backend ledger
> owns money-correctness (durable idempotency, double-entry, atomicity).**

Every item below either *validates* something the gateway currently trusts, or *anchors* an
identifier to deterministic/authenticated context instead of to the probabilistic model. We do **not**
add a durable store to the gateway to duplicate the ledger's guarantees — the ledger's unique
constraint + `IN_PROGRESS→COMPLETED` record (written in the same transaction as the payment) remains
the source of truth.

## Status scorecard

**Aug 2026:** H1, H2, H4 complete and tested; H3 core (token verification + per-request principal)
complete and tested, with the HTTP transport + RFC 9728 PRM wiring still pending. 49 tests green.

| Concern | Status |
|---|---|
| No double-charge on identical retry | ✅ deterministic key + backend dedup |
| No double-charge across instances / races | ✅ backend unique constraint + gateway fail-fast (H2) |
| Cross-instance conflict detection | ✅ shared Redis store (H2) |
| Backend error → agent taxonomy | ✅ corrected 409/422 mapping (H1) |
| Deterministic, non-LLM operation id | ✅ signeda mandate anchors idempotency (H4) |
| Flaky agent mutating args | ✅ mandate pins the exact transaction (H4) |
| Agent identity & permissions | ✅ verified token → per-request principal (H3); ⏳ HTTP transport/PRM pending |

---

## H1 — Fix backend error mapping *(quick win, no new deps)* ✅ done

**Problem.** [`backend/http_backend.py`](../src/payments_mcp/backend/http_backend.py) maps the
wallet-service's statuses wrongly. The wallet-service returns **409 = idempotency key IN_PROGRESS
(retry later)** and **422 = same key + different body (true conflict)**. Current code maps 409 →
`IDEMPOTENCY_CONFLICT` (non-retryable) and 422 → `INVALID_ARGUMENT`. So a concurrent in-flight
duplicate tells the agent "don't retry" when it should say "wait and re-check."

**Changes.** In `_raise_for_status`:
- `409` → `OPERATION_IN_PROGRESS`, `retryable=True` (taxonomy code already exists in
  [`errors.py`](../src/payments_mcp/errors.py)).
- `422`: keep the `"fund"` → `INSUFFICIENT_FUNDS` branch; add a body/idempotency-mismatch branch →
  `IDEMPOTENCY_CONFLICT`; else `INVALID_ARGUMENT`.
- Ensure `errors.to_gateway_error` sets `retry_after_seconds` for `OPERATION_IN_PROGRESS`.

**Acceptance.** `respx`-mocked backend: 409 → `OPERATION_IN_PROGRESS` (retryable); 422 body-mismatch
→ `IDEMPOTENCY_CONFLICT`; 422 funds → `INSUFFICIENT_FUNDS`. Add to `tests/test_m1_backend.py`.

**Effort:** ~1 hour · **Risk:** low · **Deps:** none.

---

## H2 — Shared conflict store + fail-fast lock (Redis) ✅ done

**Problem.** The `operation_id → args_hash` conflict map in
[`operations.py`](../src/payments_mcp/operations.py) is an in-memory dict, so cross-instance conflict
detection and fail-fast on concurrent duplicates don't work with more than one gateway process.

**Changes.**
- Introduce an `OperationStore` **protocol** (matches the existing backend-adapter style) with two
  implementations: `InMemoryOperationStore` (current behaviour; dev/tests/demo) and
  `RedisOperationStore`.
- Redis impl: conflict detection via `SET op:{id} {args_hash} NX` (+ compare on conflict); fail-fast
  via a short-TTL lock `SET lock:{id} {cid} NX EX 30`. Lock **not acquired** → surface
  `OPERATION_IN_PROGRESS` (retryable), do **not** call the backend.
- Config: `PAYMENTS_REDIS_URL` (optional). Unset → in-memory fallback, so demo/tests need no Redis.
- **Redis is a hint, never the arbiter.** Correctness still comes from the backend unique constraint,
  so we are structurally immune to the classic "SETNX-succeeded-then-DB-failed → double charge" bug.

**Acceptance.** Two `Operations` instances sharing a fake Redis: same id + different payload across
instances → `IDEMPOTENCY_CONFLICT`; concurrent duplicate → one proceeds, other gets
`OPERATION_IN_PROGRESS`. In-memory path unchanged.

**Effort:** ~0.5 day · **Risk:** medium (new async dep) · **Deps:** H1 (for the in-progress code).

---

## H3 — OAuth 2.1 resource-server auth → real `principal_id` ⏳ core done, transport pending

**Done:** `identity.py` (`TokenVerifier` HS256 with iss/aud/exp, `PrincipalStore`, `resolve_principal`
with scope narrowing); gateway resolves a per-request principal; `UNAUTHENTICATED` taxonomy code;
config `auth_secret/issuer/audience`; `server.principal_from_token` seam. **Pending:** streamable-HTTP
transport, RFC 9728 PRM endpoint, and wiring FastMCP `auth=` to call `principal_from_token` per request.

**Problem.** Identity is hardwired (`demo_principal()`); anyone who can reach the server is
"demo-agent". No authenticated caller, no per-tenant scoping.

**Changes.**
- Serve over **streamable-HTTP** transport (keep stdio + `demo_principal` behind a `--dev` flag for
  local use).
- Act as an **OAuth 2.1 resource server** (MCP Nov-2025 spec): validate the bearer JWT (issuer,
  `exp`, signature via JWKS, and **audience = the RFC 8707 resource indicator** so a token for
  another server is rejected). Return `401` + `WWW-Authenticate` pointing at the RFC 9728 Protected
  Resource Metadata document.
- **`principal_id` = verified token `sub`.** Load the `AgentPrincipal` (scopes, allow-list, ceiling)
  from a **principal store** keyed by `sub` (start with a static config map; upgrade to a service
  later). Map OAuth scopes → `payments:read/create/refund/admin`.
- **Refactor:** resolve the principal **per request** from the auth context instead of holding a
  singleton on the `Gateway`. (Today `Gateway.__init__` fixes `self.principal`; this becomes a
  per-call parameter / request-scoped lookup.) This refactor also unblocks multi-tenant.

**Acceptance.** No/invalid token → `401` + PRM pointer; expired token rejected; a token with only
`create` scope can create but not refund; `principal_id` in the audit log matches the token subject.

**Effort:** 1–2 days · **Risk:** higher (transport change + per-request principal refactor) ·
**Deps:** none strictly, but do after H1/H2 so the refactor lands on corrected behaviour.

---

## H4 — Signed payment mandate (AP2-aligned) — the deterministic, non-LLM authorization ✅ done

**Delivered as** `mandate.py` (`Mandate`, `MandateVerifier.verify`/`.enforce`, `sign_mandate`); gateway
`create_payment`/`refund_payment` accept a signed `mandate`, enforce payer/payee/currency/amount, and
anchor idempotency to `mandate_id`; codes `MANDATE_INVALID`/`MANDATE_MISMATCH`. HS256 now (secret
≥32 bytes); asymmetric / Verifiable-Credential is a drop-in on the verifier.

**Problem.** The operation id is derived from the args the *model* sent, so (a) identical legitimate
payments collapse, and (b) a hallucinated/mutated amount just creates a *different* payment —
idempotency can't catch arg-drift. There is no authorization artifact the model cannot fabricate.

**Idea (AP2's Cart-Mandate applied here).** A **signed authorization token minted by deterministic
code** (the calling app / an approval/checkout step) — never the model — pins the exact transaction:

```
Mandate {
  mandate_id     // unique  → THE idempotency anchor (business-generated, stable)
  payer, payee, currency
  max_amount_minor            // ceiling this authorization permits
  issued_at, expires_at
  issuer
  signature                   // gateway verifies; the model cannot forge one
}
```

**Changes.**
- Add a `Mandate` model + a `MandateVerifier` (start with HMAC via a shared secret — simple; leave an
  asymmetric/Verifiable-Credential upgrade path to match AP2 exactly).
- `create_payment` / `refund_payment` accept a `mandate` (signed token). The gateway:
  1. verifies signature + `expires_at`,
  2. **asserts the request matches the mandate** (amount ≤ `max_amount_minor`, payer/payee/currency
     equal) — this is what defeats agent arg-drift/hallucination,
  3. sets `idempotency_key = f(mandate_id)` — deterministic, business-anchored, model-independent.
- The model-supplied free-form `operation_id` is deprecated for money-movement tools (kept only for
  the dev/demo path). Reads stay as-is.

**Acceptance.** Forged or expired mandate → rejected; requested amount over `max_amount_minor` →
rejected; two distinct payments require two mandates; retry with the same mandate → replay (no second
charge); a hallucinated amount that disagrees with the mandate → rejected, not executed.

**Effort:** 2–3 days · **Risk:** medium-high (largest design change) · **Deps:** H3 (issuer/keys sit
naturally alongside the auth work).

---

## Sequencing

```
H1 (error mapping, ~1h, no deps)
  └─▶ H2 (Redis conflict store + fail-fast lock)
        └─▶ H3 (OAuth2.1 resource server + per-request principal)
              └─▶ H4 (signed mandate: deterministic id + arg-drift enforcement)
```

H1 is a standalone bug fix you can ship immediately. H2 is a contained optimization. H3 and H4 are the
structural, resume-worthy pieces (authenticated identity; agent-safe authorization) and share the
key/issuer machinery, so they're adjacent on purpose.

## Non-goals (deliberately out of scope)

- Building the **authorization server** or a full **AP2 Verifiable-Credential** stack — the gateway is
  a resource server / mandate *verifier*, not the issuer.
- A durable idempotency store *in the gateway* — the ledger owns that.
- Real bank/card rails, KYC/AML, multi-currency FX.

## Open decisions (need your call)

1. **Transport:** add streamable-HTTP for prod while keeping stdio+demo for local? (Assumed yes.)
2. **Mandate signing:** start with symmetric **HMAC** (simple, ship fast) and upgrade to asymmetric /
   VC later — or go asymmetric from day one to be AP2-true?
3. **Principal store:** static config map first, external service later — acceptable?
4. **Redis:** required in prod, or always optional with in-memory fallback?

## Sources

- MCP auth spec — [Descope](https://www.descope.com/blog/post/mcp-auth-spec) ·
  [Auth0](https://auth0.com/blog/mcp-specs-update-all-about-auth/) ·
  [MojoAuth (RFC 9728 / 8707)](https://mojoauth.com/blog/how-mcp-authorization-actually-works-oauth-2-1-resource-servers-and-resource-indicators)
- [Stripe — Designing robust APIs with idempotency](https://stripe.com/blog/idempotency)
- Agentic payments — [Orium: ACP / AP2 / x402](https://orium.com/blog/agentic-payments-acp-ap2-x402) ·
  [PaymentBrief: MCP + Stripe toolkit](https://paymentbrief.com/articles/ai-agents-payment-apis-mcp-stripe-toolkit/)
- [Race conditions in idempotent payment operations](https://medium.com/@ankurnitp/handling-race-conditions-in-idempotent-operations-a-practical-guide-for-payment-systems-eb045b9ca7c4) ·
  [Redis distributed locks (SETNX / Redlock)](https://leapcell.io/blog/implementing-distributed-locks-with-redis-delving-into-setnx-redlock-and-their-controversies)
