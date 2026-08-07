"""The object that flows along the edges of a run.

Specified by [ADR 0002](../docs/adr/0002-flow-topology.md). Everything here crosses the
workflow/activity boundary, so it is serialisable, versioned, and free of behaviour.

Secrets never appear in `FlowState`. Credentials are resolved at the point of use by the
Flow Engine — see [ADR 0001](../docs/adr/0001-flow-engine-control-plane.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

# Bump on every breaking change to the shape below. Fields are added and deprecated,
# never removed in place — a suspended run may be carrying an older shape.
SCHEMA_VERSION = 1

RiskTier = Literal["low", "medium", "high"]

# Ordered, so that "may only be raised, never lowered" is expressible as a comparison
# rather than as a rule someone has to remember.
RISK_TIER_ORDER: dict[RiskTier, int] = {"low": 0, "medium": 1, "high": 2}


class NodeClass(StrEnum):
    """Whether a node may call a model, and under what conditions.

    Enforced rather than documented — see `engine.nodes.base`.
    """

    DETERMINISTIC = "deterministic"
    ADVISORY = "advisory"
    GENERATIVE = "generative"
    VERIFIER = "verifier"


@dataclass(frozen=True)
class Ticket:
    """Hostile input. Anyone who can file a ticket can write text a model will read."""

    id: str
    system: str
    title: str
    body: str
    acceptance_criteria: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    story_points: int | None = None


@dataclass(frozen=True)
class RepoPin:
    name: str
    path: str
    commit: str


@dataclass(frozen=True)
class Workspace:
    """One or more repositories, each pinned to a SHA — ADR 0005.

    A repository is not the unit. Contract-coupled changes across services are authored in
    one context, because splitting them produces interface drift that each side's tests pass.
    """

    repos: list[RepoPin] = field(default_factory=list)


@dataclass(frozen=True)
class ChangePlan:
    summary: str
    steps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FileChange:
    path: str
    added: int
    removed: int


@dataclass(frozen=True)
class ProposedEdit:
    """A file the Coder wants written.

    Carried on the state because the sandbox produces a diff and never pushes it -- the Flow
    Engine pushes, under a separate identity (ADR 0005 §3, property 5). Release therefore
    needs the content, and needs it to be exactly what Build & Test inspected rather than
    something regenerated a step later.
    """

    path: str
    content: str


@dataclass(frozen=True)
class Diff:
    files: list[FileChange] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


@dataclass(frozen=True)
class CIResult:
    """A reality anchor. `exit_code` is the verdict; nothing else is."""

    exit_code: int
    url: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class SASTResult:
    """A reality anchor."""

    high: int = 0
    medium: int = 0
    low: int = 0
    report_url: str | None = None


@dataclass(frozen=True)
class Verification:
    verifier: str
    passed: bool
    findings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Approval:
    """Records the evidence the approver was shown, not merely that they clicked approve.

    An approval detached from what was on screen is not evidence of review — ADR 0003 §6.
    """

    principal: str
    approved: bool
    risk_tier: RiskTier
    evidence_digest: str
    comment: str = ""


@dataclass(frozen=True)
class Artifact:
    kind: str
    uri: str
    digest: str


@dataclass
class FlowState:
    """`node: (FlowState) -> FlowState`.

    The uniform signature is what lets any node later be replaced by a child flow without
    changing its callers — ADR 0002, "Recursive composition".
    """

    run_id: UUID
    root_run_id: UUID
    ticket: Ticket

    # Pinned at run start and inherited unchanged by child runs, so that an audit record
    # remains interpretable without the policy repository still being reachable — ADR 0003.
    # Not in the ADR 0002 field list, which predates policy pinning.
    policy_commit: str
    policy_bundle: dict[str, Any]

    schema_version: int = SCHEMA_VERSION
    parent_run_id: UUID | None = None

    risk_tier: RiskTier = "low"
    # Kept apart from `risk_tier` so the escalation is visible in the audit record rather
    # than only its result. Tiering happens twice and the second is authoritative.
    provisional_risk_tier: RiskTier | None = None

    workspace: Workspace | None = None
    plan: ChangePlan | None = None
    branch: str | None = None
    diff: Diff | None = None
    proposed_edits: list[ProposedEdit] = field(default_factory=list)

    ci_result: CIResult | None = None
    sast_result: SASTResult | None = None
    coverage: float | None = None
    verifications: list[Verification] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)

    # Divided among child runs, never duplicated. Without this, fan-out later becomes an
    # unbounded billing event — ADR 0002.
    budget_cents_allowed: int = 0
    budget_cents_spent: int = 0
    retry_count: int = 0
    artifacts: list[Artifact] = field(default_factory=list)
