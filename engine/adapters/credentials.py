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

from engine.errors import InvariantViolation, PolicyDenied


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
    #: Read a pipeline verdict back. Kept apart from `CI_TRIGGER`, which is privileged: being
    #: able to *observe* someone else's pipeline is not being able to *run* one, and the whole
    #: value of CI as a reality anchor is that KuWarden cannot influence it.
    CI_READ = "ci.read"
    CI_TRIGGER = "ci.trigger"
    DEPLOY = "deploy"
    LLM_API_KEY = "llm.api_key"


#: The credentials invariant 2 says an agent node never holds: CI, merge, deploy. Written out
#: rather than derived from a naming convention, so widening the set is a visible edit in a
#: diff instead of a side effect of naming a new enum member.
PRIVILEGED_KINDS: frozenset[CredentialKind] = frozenset(
    {CredentialKind.SCM_MERGE, CredentialKind.CI_TRIGGER, CredentialKind.DEPLOY}
)


def assert_may_hold(kind: CredentialKind) -> None:
    """Refuse a privileged credential to a node that contains a model — invariant 2.

    The predicate is `may_call_llm`, the same property invariant 1 uses. Reusing it is the
    point: "agent node" then has one definition, and classifying a new node cannot satisfy one
    invariant while quietly breaking the other.

    Code with no node context is Flow Engine plumbing, the Workbench, or a test — all of which
    may hold these. Node ⑦ Release is `deterministic` and is the one that legitimately deploys
    under integration model A (ADR 0004 §4).
    """
    if kind not in PRIVILEGED_KINDS:
        return
    # Imported here, not at module scope: `engine.nodes.base` executes the `engine.nodes`
    # package, which imports the nodes, which import this module. Same cycle, same fix, as
    # `assert_may_call_llm`.
    from engine.nodes.base import current_node

    spec = current_node()
    if spec is not None and spec.may_call_llm:
        raise InvariantViolation(
            f"node {spec.id!r} is classified {spec.node_class.value!r} and contains a model; "
            f"it may not hold a {kind.value!r} credential (invariant 2)"
        )


@dataclass(frozen=True)
class CredentialRequest:
    """A request for one credential, checked against invariant 2 at construction.

    The check lives here rather than in each `CredentialBroker.resolve` because there are
    three broker implementations and tests inject more. Three copies of a security control
    drift, and a test double would bypass it entirely — every path constructs one of these.
    """

    kind: CredentialKind
    # The platform instance this is for -- an org, a project, a host. Keeps one tenant's
    # token from being resolvable for another's resources.
    realm: str

    def __post_init__(self) -> None:
        assert_may_hold(self.kind)


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
        # On GitHub the Actions API is part of the repository API, so the read capability
        # genuinely rides the same token. The *kind* stays separate — that is what the store
        # and invariant 2 key on — and a platform whose CI is a separate system (Jenkins,
        # TeamCity) will want its own variable here rather than this convenience.
        CredentialKind.CI_READ: "KUWARDEN_SCM_TOKEN",
        CredentialKind.CI_TRIGGER: "KUWARDEN_CI_TOKEN",
        CredentialKind.DEPLOY: "KUWARDEN_DEPLOY_TOKEN",
        CredentialKind.LLM_API_KEY: "KUWARDEN_LLM_API_KEY",
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
