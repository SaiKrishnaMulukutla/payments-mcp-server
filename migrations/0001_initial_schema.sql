-- Bharat Connect MVP schema. PostgreSQL only.
-- Apply with a migration runner after reviewing the deployment environment.

CREATE TABLE billers (
    biller_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    fetched_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE registered_accounts (
    id UUID PRIMARY KEY,
    biller_id TEXT NOT NULL REFERENCES billers(biller_id),
    account_fingerprint BYTEA NOT NULL,
    encrypted_customer_params BYTEA NOT NULL,
    masked_identifier TEXT NOT NULL,
    customer_name TEXT,
    owner_confirmed_at TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (biller_id, account_fingerprint)
);

CREATE TABLE fetched_bills (
    id UUID PRIMARY KEY,
    registered_account_id UUID NOT NULL REFERENCES registered_accounts(id),
    biller_id TEXT NOT NULL REFERENCES billers(biller_id),
    provider_fetch_ref TEXT NOT NULL UNIQUE,
    amount_paise BIGINT NOT NULL CHECK (amount_paise > 0),
    currency CHAR(3) NOT NULL DEFAULT 'INR' CHECK (currency = 'INR'),
    customer_name TEXT,
    bill_number TEXT,
    due_at TIMESTAMPTZ,
    provider_snapshot JSONB NOT NULL,
    snapshot_sha256 CHAR(64) NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payment_intents (
    id UUID PRIMARY KEY,
    fetched_bill_id UUID NOT NULL REFERENCES fetched_bills(id),
    registered_account_id UUID NOT NULL REFERENCES registered_accounts(id),
    amount_paise BIGINT NOT NULL CHECK (amount_paise > 0),
    fee_paise BIGINT NOT NULL DEFAULT 0 CHECK (fee_paise >= 0),
    currency CHAR(3) NOT NULL DEFAULT 'INR' CHECK (currency = 'INR'),
    state TEXT NOT NULL CHECK (state IN ('created','awaiting_approval','approved','submitted','pending','succeeded','failed','unknown')),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payment_approvals (
    id UUID PRIMARY KEY,
    payment_intent_id UUID NOT NULL UNIQUE REFERENCES payment_intents(id),
    account_id UUID NOT NULL REFERENCES registered_accounts(id),
    snapshot_sha256 CHAR(64) NOT NULL,
    amount_paise BIGINT NOT NULL CHECK (amount_paise > 0),
    fee_paise BIGINT NOT NULL DEFAULT 0 CHECK (fee_paise >= 0),
    approval_token_hash CHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payment_attempts (
    id UUID PRIMARY KEY,
    payment_intent_id UUID NOT NULL REFERENCES payment_intents(id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    provider_reference TEXT,
    provider_status TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('submitted','pending','succeeded','failed','unknown')),
    request_reference TEXT NOT NULL UNIQUE,
    response_snapshot JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    UNIQUE (payment_intent_id, attempt_number)
);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_id TEXT,
    correlation_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_fetched_bills_account_time ON fetched_bills (registered_account_id, fetched_at DESC);
CREATE INDEX idx_payment_intents_state_time ON payment_intents (state, updated_at);
CREATE INDEX idx_payment_attempts_intent_time ON payment_attempts (payment_intent_id, started_at DESC);
CREATE INDEX idx_audit_events_resource_time ON audit_events (resource_id, occurred_at DESC);
