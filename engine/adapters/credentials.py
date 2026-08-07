"""The credential boundary.

Privileged credentials are resolved by the Flow Engine at the point of use. They are never
carried on `FlowState`, never handed to a node, and never present in a process that has an
LLM in it — ADR 0001. The sandbox holds none at all, ever — ADR 0005.

`Secret` exists because the realistic leak is not malice, it is a log line. A token that
reaches an audit row or a traceback has escaped, and audit rows are append-only, so it has
escaped permanently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from engine.errors import PolicyDenied


class Secret:
    """A string that refuses to render itself.

    Not encryption and not a substitute for a secret store — it removes the accidental
    paths: f-strings, `repr` in a traceback, `json.dumps`, a careless log call.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """The only way out. Grep for this to audit every use."""
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and self._value == other._value

    def __hash__(self) -> int:
        raise TypeError("Secret is not hashable; hashing it would leak it into caches")


class CredentialKind(StrEnum):
    """What a credential is for, so that grants stay narrow and reviewable."""

    TICKET_READ_WRITE = "ticket.read_write"
    SCM_READ = "scm.read"
    SCM_WRITE_BRANCH = "scm.write_branch"
    SCM_PULL_REQUEST = "scm.pull_request"
    SCM_MERGE = "scm.merge"
    CI_TRIGGER = "ci.trigger"
    DEPLOY = "deploy"


@dataclass(frozen=True)
class CredentialRequest:
    kind: CredentialKind
    # The platform instance this is for -- an org, a project, a host. Keeps one tenant's
    # token from being resolvable for another's resources.
    realm: str


class CredentialBroker(Protocol):
    async def resolve(self, request: CredentialRequest) -> Secret: ...


class EnvCredentialBroker:
    """Development broker: reads from the environment.

    Deliberately not the production answer. A real deployment resolves against the
    enterprise secret store under a workload identity (ADR 0003 §2), and this class exists so
    that the *boundary* is real from the first commit even though the backing store is not.

    Nothing here is ever written to a file, an audit row, or `FlowState`.
    """

    #: Kept explicit rather than derived from the enum, so that adding a capability is a
    #: deliberate act with a visible variable name rather than an accident of naming.
    VARIABLES: dict[CredentialKind, str] = {
        CredentialKind.TICKET_READ_WRITE: "KUWARDEN_TICKET_TOKEN",
        CredentialKind.SCM_READ: "KUWARDEN_SCM_TOKEN",
        CredentialKind.SCM_WRITE_BRANCH: "KUWARDEN_SCM_TOKEN",
        CredentialKind.SCM_PULL_REQUEST: "KUWARDEN_SCM_TOKEN",
        CredentialKind.SCM_MERGE: "KUWARDEN_SCM_MERGE_TOKEN",
        CredentialKind.CI_TRIGGER: "KUWARDEN_CI_TOKEN",
        CredentialKind.DEPLOY: "KUWARDEN_DEPLOY_TOKEN",
    }

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else dict(os.environ)

    async def resolve(self, request: CredentialRequest) -> Secret:
        name = self.VARIABLES[request.kind]
        # Realm-scoped override first, so one process can serve two orgs without one org's
        # token being reachable for the other's resources.
        scoped = f"{name}__{_realm_slug(request.realm)}"
        value = self._environ.get(scoped) or self._environ.get(name)
        if not value:
            raise PolicyDenied(
                f"no credential available for {request.kind.value} in realm {request.realm!r} "
                f"(looked for {scoped}, then {name})"
            )
        return Secret(value)


def _realm_slug(realm: str) -> str:
    return "".join(c.upper() if c.isalnum() else "_" for c in realm)
