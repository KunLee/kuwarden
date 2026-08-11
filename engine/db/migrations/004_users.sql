-- 004 — Workbench users.
--
-- Identity is not a hardening item here. ADR 0003's `no-agent-self-approval` and
-- `prod-requires-two-humans` constraints require approvers to be real, distinct,
-- identifiable humans; without a users table an approval gate records that *something*
-- clicked approve, which is not evidence of review.
--
-- Local accounts rather than an external IdP: the flagship deployment is air-gapped and can
-- reach no OIDC provider. An enterprise IdP adapter is a later addition, not a replacement.

CREATE TABLE users (
    id            UUID PRIMARY KEY,
    -- Case-insensitive, because "Alice@acme.com" and "alice@acme.com" being two accounts is
    -- a way to accidentally satisfy prod-requires-two-humans with one person.
    email         TEXT NOT NULL UNIQUE CHECK (email = lower(email)),
    display_name  TEXT NOT NULL,

    -- Argon2id. The full encoded string, including its parameters, so a future parameter
    -- change can re-hash on next login instead of invalidating every password.
    password_hash TEXT NOT NULL,

    -- ADR 0003 §1: viewer reads, approver decides at gates, admin configures.
    role          TEXT NOT NULL CHECK (role IN ('viewer', 'approver', 'admin')),

    -- Bumped to invalidate every existing session for this user. Same instinct as ADR 0003's
    -- deny-wins revocation: disabling an account must take effect immediately, not whenever
    -- the cookie happens to expire.
    token_version INT NOT NULL DEFAULT 1,
    disabled_at   TIMESTAMPTZ,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE INDEX users_active_idx ON users (email) WHERE disabled_at IS NULL;
