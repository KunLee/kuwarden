-- 002 — tenant credentials, encrypted at rest.
--
-- ADR 0006. The engine's own database password and master key are NOT here; they are
-- bootstrap configuration and cannot live in the thing they are needed to open.

CREATE TABLE app_credentials (
    app_id      UUID NOT NULL REFERENCES app_registry(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,

    -- Fingerprint of the key that encrypted this row. Without it a new master key means
    -- every credential has to be re-entered by hand, because nothing records which key
    -- produced which ciphertext.
    key_id      TEXT  NOT NULL,
    nonce       BYTEA NOT NULL,
    ciphertext  BYTEA NOT NULL,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (app_id, kind)
);

-- A operator answering "is the SCM token set for this app?" must never need the value.
COMMENT ON COLUMN app_credentials.ciphertext IS
    'AES-256-GCM. Associated data binds this row to (app_id, kind), so a ciphertext moved '
    'between rows fails to decrypt rather than silently granting one app another''s access.';
