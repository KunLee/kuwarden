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
    #: The workflow state the ticket was in when Triage read it — "Active", "Ready for Agent".
    #: Admission may require a specific one, so that starting work is something a human did
    #: deliberately rather than something inferred from a save.
    state: str | None = None
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

    A removal is an edit. `deleted` carries it with an empty `content`, because the
    alternative — a change that only removes files having nothing to represent it — made a
    legitimate deletion indistinguishable from the Coder having done nothing at all.
    """

    path: str
    content: str
    deleted: bool = False


@dataclass(frozen=True)
class RiskRules:
    """The tiering rules, as data the workflow can be given rather than config it must read.

    ADR 0002 splits tiering into two stages, and the second one runs in workflow code — which
    is deterministic and may not touch the filesystem. Passing the rules in `FlowInput` means
    a replay re-derives the same tier from the same inputs, and a rule edited mid-run cannot
    retroactively change a decision already recorded.

    Empty everywhere is legal and means "no rule escalates". That is the honest default: an
    application that wants every change treated as low risk should have said so, not inherited
    it from a list nobody filled in.
    """

    #: Globs that make a change `high` whatever else is true — authn, payments, migrations.
    high_paths: tuple[str, ...] = ()
    #: Globs that make a change `medium`.
    medium_paths: tuple[str, ...] = ()
    #: Above this many changed files, one human looks at it.
    medium_changed_files: int | None = None
    #: Above this many changed files, the change stops being small whatever it touched.
    high_changed_files: int | None = None


@dataclass(frozen=True)
class Diff:
    files: list[FileChange] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


#: Who executed the tests. `ci` is the project's own pipeline — an external system of record,
#: which is what invariant 3 asks for. `sandbox` is KuWarden's own container: good enough to
#: drive the Coder's inner loop, and *not* an independent witness, because the same system
#: both produced the change and graded it.
VerdictSource = Literal["sandbox", "ci"]


@dataclass(frozen=True)
class CIResult:
    """A reality anchor. `exit_code` is the verdict; nothing else is.

    `source` is required and never defaulted, for the same reason `control_mode` is not
    (ADR 0004): the two sources are not equally strong evidence, and silently presenting one
    as the other overstates what was verified.
    """

    exit_code: int
    #: Where the exit code came from. Invariant 3 wants an external system of record; the
    #: sandbox is *ours*, so a `sandbox` verdict is a deviation that must travel with the
    #: verdict rather than be assumed away at the point it is read.
    source: VerdictSource
    url: str | None = None
    duration_ms: int = 0

    @property
    def is_external_anchor(self) -> bool:
        """Whether this verdict satisfies invariant 3 without qualification."""
        return self.source == "ci"


@dataclass(frozen=True)
class SASTResult:
    """A reality anchor."""

    high: int = 0
    medium: int = 0
    low: int = 0
    report_url: str | None = None


@dataclass(frozen=True)
class Verification:
    """One verifier's verdict, and the findings the verdict was computed from.

    `passed` is derived: a finding graded `blocking` fails the change. The model no longer
    returns a verdict of its own, because for three changes in one week it wrote down the
    reason a change should not ship and passed it anyway.
    """

    verifier: str
    passed: bool
    #: Human-readable, each prefixed with its severity. What the notes and the ticket show.
    findings: list[str] = field(default_factory=list)
    #: The same findings structured — `{detail, severity}`. What the evidence document reads,
    #: so an approver is shown which findings were blocking and which were not.
    graded: list[dict[str, str]] = field(default_factory=list)


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

    #: The registered application this run is for, as the Workbench knows it. Carried so a
    #: node can check it against the configuration it was handed — a worker serves exactly one
    #: application's `kuwarden.yaml`, and without this nothing would notice a mismatch. Empty
    #: means the run predates the check, which is treated as "cannot verify", not as "fine".
    app_name: str = ""

    risk_tier: RiskTier = "low"
    # Kept apart from `risk_tier` so the escalation is visible in the audit record rather
    # than only its result. Tiering happens twice and the second is authoritative.
    provisional_risk_tier: RiskTier | None = None

    workspace: Workspace | None = None
    plan: ChangePlan | None = None
    branch: str | None = None
    # The default branch, and the commit its tree was read at. Pinned once, by the Coder, and
    # never re-resolved: Push builds on this commit and Release targets this branch, so a
    # default branch that moves mid-run cannot change what was reviewed.
    base_branch: str | None = None
    base_commit: str | None = None
    # The tip of the pushed branch, or `None` while nothing has been pushed. Release refuses
    # without it — a pull request for a branch nobody pushed asks a human to review nothing.
    head_commit: str | None = None
    diff: Diff | None = None
    proposed_edits: list[ProposedEdit] = field(default_factory=list)

    # Whether the sandbox that executed model-written code was fully isolated. `None` means
    # nothing was executed. Recorded because it is a property of the run, not of the machine
    # at the moment someone looks: a report exported next year must still say under which
    # isolation the change was produced.
    sandbox_isolation: Literal["enforced", "degraded"] | None = None
    sandbox_gaps: list[str] = field(default_factory=list)

    # The authoritative verdict. `source` says who produced it, and the two are not equally
    # strong evidence — see `CIResult`.
    ci_result: CIResult | None = None
    # The sandbox's own verdict, kept even when CI produced the authoritative one. Where the
    # two disagree that is itself a finding — environment drift, a missing dependency, a test
    # that only passes locally — and it would be lost if CI simply overwrote it.
    sandbox_result: CIResult | None = None
    # Why no CI verdict is available, or how the one on `ci_result` was reached. Always
    # recorded when a CI adapter is configured, because "we did not check" must never be
    # indistinguishable from "we checked and it was fine".
    ci_detail: str | None = None
    sast_result: SASTResult | None = None
    coverage: float | None = None
    verifications: list[Verification] = field(default_factory=list)
    #: Which rule settled the authoritative tier, in the words the rule is written in — e.g.
    #: "app/layout.tsx matches high_paths '**/layout.*'".
    #:
    #: Carried rather than left in the audit event because the people most confused by an
    #: escalation are the ones reading the *ticket*, not the trail. A change described as a
    #: theme switch arriving as "risk tier high, two approvers required" reads as the system
    #: being arbitrary unless the sentence that decided it travels with the number.
    risk_tier_reason: str = ""
    #: Verifiers that falsified the change and were not permitted to block it. Empty on a run
    #: where every verifier that objected was allowed to stop it — which is not the same as a
    #: run where nothing objected, and the difference belongs in the report.
    advisory_objections: list[str] = field(default_factory=list)
    #: The verifiers that actually stopped the change — the falsifying ones the application
    #: permits to block. Set by the flow immediately before compensation and empty for every
    #: other abort (a node failure, an approver's rejection), where claiming a verifier caused
    #: it would be worse than saying nothing.
    #:
    #: Needed because `verifications` cannot answer the question on its own: an advisory
    #: verifier's falsification looks identical to a blocking one there, and naming a disarmed
    #: verifier as the cause of a rejection is the one wrong answer that sends a reader
    #: looking for a fault in the control instead of at the reviews that objected.
    rejected_by: list[str] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)

    # Divided among child runs, never duplicated. Without this, fan-out later becomes an
    # unbounded billing event — ADR 0002.
    budget_cents_allowed: int = 0
    #: Estimated spend so far, in micro-cents — see `engine.policy.pricing`. Micro rather
    #: than whole cents because the previous integer-cent counter floored every call to at
    #: least one cent, which made a 123,000-token call and a 10,000-token call identical
    #: and hid the only number worth watching.
    #:
    #: `None` means at least one call used a model nobody has priced. Not zero: an
    #: unmeasured cost must never read as a free one.
    spend_micro_cents: int | None = 0
    #: How many times the *Coder's own loop* retried inside one activity, after reading a
    #: failing test run. Written by the Coder, redacted from verifiers (invariant 4).
    retry_count: int = 0
    #: Which pass of the ③⇄④ cycle this is — Coder, Push, Build & Test, and back.
    #:
    #: Separate from `retry_count` because the two loops are different, and sharing one field
    #: silently broke pushing. The Coder's inner loop assigns from 0 on every invocation, so
    #: it overwrote whatever the outer loop had set — and since `retry_count` is the
    #: `kuwarden-attempt` trailer, and that trailer is the SCM adapters' idempotency key, the
    #: second pass produced a byte-identical commit message. The adapter matched it against
    #: the branch tip, concluded the push had already landed, and returned without pushing.
    #:
    #: The run then read CI back for the *previous* commit, got the same failure, and looped —
    #: grading the first attempt's code until the retry budget ran out, while Push's own record
    #: claimed it had pushed the new files.
    push_attempt: int = 0
    # What compensation did, or could not do. Carried on the state so the flow can put it
    # in the audit trail: a cleanup that silently failed leaves a branch on someone's
    # remote with nothing anywhere saying why.
    #: Set by Release when KuWarden merged the pull request itself — ADR 0004 model B. The
    #: flow reads it to emit the `external_effect` row carrying `control_mode="authorized"`,
    #: so this field is the only thing that turns a merge into a claim of authority. None
    #: means no merge was performed, never "we did not check".
    merged_commit: str | None = None
    cleanup: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)

    # What the executing node read, decided and produced — see `engine.nodes.notes`. Written by
    # the node, drained by the flow into `node_completed`, and cleared before the next node
    # runs: notes describe one execution, and carrying them forward would attribute one node's
    # reasoning to the next one's record.
    #
    # Not in `VERIFIER_MAY_SEE`, so the brief clears it. A verifier reading the Planner's prompt
    # out of a note would defeat invariant 4 as thoroughly as reading `plan` directly.
    #
    # Never contains a credential. The rest of `FlowState` excludes secrets by construction;
    # this field is free-form, so the rule has to be stated rather than enforced by shape.
    notes: dict[str, Any] = field(default_factory=dict)
