# ADR 0006 — Credential storage

- **Status:** Accepted
- **Date:** 2026-08-08
- **Depends on:** [ADR 0001](0001-flow-engine-control-plane.md), [ADR 0003](0003-role-graph-and-traceability.md)
- **Constrains:** `engine/adapters/credentials.py`, PostgreSQL schema, Workbench registration

---

## Context

[ADR 0003](0003-role-graph-and-traceability.md) §2 says an agent node's identity is a
verifiable workload identity and that credentials are resolved at the point of use. It did
not say **where the credentials live**, because until an application could be registered at
runtime the question did not arise: every credential was an environment variable set before
the process started.

Two things make it arise now.

**The write path.** The Workbench registers an application while the engine is running. A
user saves a PAT and the next run must be able to use it. Environment variables are fixed at
process start, so they cannot serve a runtime write.

**Two different kinds of credential get confused.** They have different constraints and must
not share a mechanism:

| | What | Where it can live |
|---|---|---|
| **Bootstrap** | KuWarden's own database password, and the key below | Environment only. It cannot live in the database, because reading the database requires it |
| **Tenant** | A registered application's SCM token, ticket token, LLM API key | Somewhere writable at runtime |

---

## Decision

**Tenant credentials are encrypted with a local master key and stored in PostgreSQL. The
storage layer sits behind a Protocol so that it can be replaced without touching any caller.**

### 1. Envelope encryption with a local master key

- `KUWARDEN_SECRET_KEY` — 32 bytes, base64url, from the environment. **Required, never
  defaulted**, on the same rule as every other credential here: a value that works out of
  the box is a value nobody replaces.
- **AES-256-GCM.** Authenticated, so a tampered ciphertext fails to decrypt rather than
  yielding rubbish that some caller then sends to a platform.
- **Associated data binds each ciphertext to its slot** — `app_id` and credential kind are
  authenticated but not encrypted. Without this, anyone with write access to the database
  could move one application's ciphertext into another application's row and it would
  decrypt cleanly, silently granting the second application the first one's access. With
  it, that row fails to decrypt. This is cheap now and impossible to retrofit onto
  ciphertexts already written.
- **`key_id` is stored beside each ciphertext** — a short fingerprint of the key that
  encrypted it. Rotation is otherwise impossible: without knowing which key produced which
  row, a new key means every credential must be re-entered by hand.

### 2. What this protects against, stated precisely

| Threat | Protected |
|---|---|
| Database exfiltration, a stolen backup, a misconfigured read replica | **Yes** — ciphertext without the key is useless |
| A `SELECT` through an application-level SQL injection | **Yes** |
| Ciphertext moved between rows by someone with database write access | **Yes** — associated data |
| **Host compromise** | **No.** The key is on the host, in the environment |

The last row is the honest limit and the reason this is a first implementation rather than
the final one. It is stated here so nobody later mistakes this for protection it does not
provide.

### 3. The Protocol is the swap point

```python
class CredentialBroker(Protocol):     # read — every node path uses this
    async def resolve(self, request: CredentialRequest) -> Secret: ...

class CredentialStore(CredentialBroker, Protocol):   # read + write — Workbench only
    async def put(self, app_id: UUID, kind: CredentialKind, secret: Secret) -> None: ...
    async def forget(self, app_id: UUID, kind: CredentialKind) -> None: ...
```

Nothing in `nodes/` or `flows/` sees anything but `resolve`. Replacing local storage with
AWS Parameter Store, Azure Key Vault, or HashiCorp Vault is one new class and one line of
configuration.

**Write is a separate interface deliberately.** The engine reads credentials; only the
Workbench writes them. A node that could call `put` is a node that could grant itself
access.

### 4. Write-only from the outside

There is no read-back API. The Workbench can store a credential and can report that one
exists, and cannot return its value. A credential that can be read back through the UI is a
credential that eventually is.

---

## Consequences

### What this buys

- Works air-gapped, which is the deployment the product is positioned for. A cloud secret
  store is unreachable there; this is not.
- No external dependency, no additional infrastructure, no per-secret cost.
- The Workbench's registration flow becomes possible, which was the blocking item.
- A database backup is not a credential breach.

### What this costs

- **Losing `KUWARDEN_SECRET_KEY` loses every stored credential.** They must be re-entered.
  This must be in the disaster-recovery runbook, and the key must be backed up separately
  from the database — backing it up *with* the database defeats the encryption entirely.
- Key rotation is possible but not automatic: re-encrypting existing rows under a new key is
  an operation someone has to write and run.
- The host-compromise gap above is real, and for a customer whose threat model includes it
  the answer is a different `CredentialStore`, not a tweak to this one.

### What we now owe

- A rotation procedure, before the first customer has enough credentials for re-entry to be
  painful.
- A `CredentialStore` implementation backed by a real secret manager, for customers who have
  one. AWS Parameter Store is the cheapest first one — `SecureString` is free at standard
  tier, versioned, and IAM-scoped per path prefix, which turns the realm scoping already in
  `engine/adapters/credentials.py` into something the platform enforces.
- `THREAT_MODEL.md` must carry the host-compromise limit explicitly.

---

## Alternatives considered

### Store credentials in plaintext in PostgreSQL

*Rejected outright.* The audit trail is append-only by design, so a credential that leaks
into it has leaked permanently. It would also make a routine database backup a credential
breach.

### Require an external secret manager from the start

*Rejected as a requirement, retained as an option.* It is the stronger design and it is what
a large customer will want. As the *only* option it makes an air-gapped install impossible,
and that install is the product's flagship scenario — the one thing competitors cannot
serve.

### Put the master key in the database alongside the ciphertext

*Rejected.* That is not encryption, it is encoding. The key and the ciphertext must fail
independently, which is the whole point of the split.

### Derive the key from a passphrase at startup

*Rejected for now.* It removes the key file at the cost of an interactive prompt, which
makes unattended restart impossible. Revisit if operators ask for it — a KDF over a
passphrase held in the platform's own secret mechanism is a reasonable middle ground.

---

## Revisit triggers

- A customer's threat model includes host compromise.
- Credential count reaches the point where manual re-entry after key loss is unacceptable.
- Any customer already operates Vault or a cloud secret manager — then use it rather than
  duplicating its job.

---

## References

- [ADR 0001](0001-flow-engine-control-plane.md) · [ADR 0003](0003-role-graph-and-traceability.md)
- `engine/adapters/credentials.py` — the Protocol and the `Secret` wrapper
