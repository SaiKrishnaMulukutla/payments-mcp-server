-- Run once against the gateway's Postgres database before enabling production approval storage.
CREATE TABLE payment_approvals (
    approval_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    requester_principal_id TEXT NOT NULL,
    mandate_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('create_payment', 'refund_payment')),
    amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL,
    payer TEXT,
    payee TEXT,
    payment_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CONSUMED', 'EXPIRED')),
    decision TEXT CHECK (decision IN ('APPROVED', 'REJECTED')),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ,
    decided_by_principal_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL,
    updated_by_principal_id TEXT NOT NULL,
    consumed_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, operation_id),
    CHECK (
        (action = 'create_payment' AND payer IS NOT NULL AND payee IS NOT NULL AND payment_id IS NULL)
        OR (action = 'refund_payment' AND payment_id IS NOT NULL AND payer IS NULL AND payee IS NULL)
    )
);

CREATE INDEX payment_approvals_pending_by_tenant
    ON payment_approvals (tenant_id, created_at)
    WHERE status = 'PENDING';
