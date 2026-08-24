"""The invariants from CLAUDE.md, as tests rather than as prose.

A governance rule that only exists in a document is a rule that will be violated without
anyone noticing.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from engine.adapters.credentials import PRIVILEGED_KINDS, CredentialKind, CredentialRequest
from engine.adapters.llm import assert_may_call_llm
from engine.errors import InvariantViolation, RiskTierLowered
from engine.flows.delivery import _verifier_brief
from engine.nodes import NODES, REGISTRY, repo_context, verifiers
from engine.nodes.base import executing
from engine.policy.protected_paths import DEFAULT_PROTECTED_PATHS, ProtectedPaths
from engine.policy.tiering import assert_not_lowered, raise_to, required_approvals
from engine.sandbox import ResourceLimits, SandboxCapabilities, Workspace
from engine.sandbox.podman import PodmanSandbox
from engine.state import ChangePlan, FlowState, NodeClass, ProposedEdit, Ticket

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- invariant 1: the Flow Engine contains no LLM ----------------------------------------


def test_flow_engine_may_not_call_a_model() -> None:
    """No node context means the caller is engine plumbing, which never gets a model."""
    with pytest.raises(InvariantViolation, match="outside any node"):
        assert_may_call_llm()


@pytest.mark.parametrize(
    "node_id", [n for n, s in REGISTRY.items() if s.node_class is NodeClass.DETERMINISTIC]
)
def test_deterministic_nodes_may_not_call_a_model(node_id: str) -> None:
    with executing(REGISTRY[node_id]), pytest.raises(InvariantViolation, match="may not call"):
        assert_may_call_llm()


@pytest.mark.parametrize(
    "node_id", [n for n, s in REGISTRY.items() if s.node_class is not NodeClass.DETERMINISTIC]
)
def test_generative_and_verifier_nodes_pass_the_guard(node_id: str) -> None:
    with executing(REGISTRY[node_id]):
        assert_may_call_llm()


# --- invariant 2: agent nodes never hold CI, merge, or deploy credentials -----------------


@pytest.mark.parametrize("kind", sorted(PRIVILEGED_KINDS))
@pytest.mark.parametrize(
    "node_id", [n for n, s in REGISTRY.items() if s.node_class is not NodeClass.DETERMINISTIC]
)
def test_a_node_containing_a_model_may_not_hold_a_privileged_credential(
    node_id: str, kind: CredentialKind
) -> None:
    """Every generative and verifier node, against every privileged kind.

    Parametrised over the registry rather than naming the Coder, so a node added later is
    covered without anyone remembering to extend this.
    """
    with executing(REGISTRY[node_id]), pytest.raises(InvariantViolation, match="invariant 2"):
        CredentialRequest(kind=kind, realm="acme")


@pytest.mark.parametrize(
    "kind",
    [CredentialKind.SCM_READ, CredentialKind.SCM_WRITE_BRANCH, CredentialKind.LLM_API_KEY],
)
def test_an_agent_node_still_gets_what_it_legitimately_needs(kind: CredentialKind) -> None:
    """The Coder reads code, writes its own branch, and calls a model. Invariant 2 stops there."""
    with executing(REGISTRY["coder"]):
        CredentialRequest(kind=kind, realm="github.com:acme")


@pytest.mark.parametrize("kind", sorted(PRIVILEGED_KINDS))
def test_a_deterministic_node_may_hold_a_privileged_credential(kind: CredentialKind) -> None:
    """Otherwise this is not a tightening, it is a broken product.

    Node ⑦ Release is `deterministic` and is what actually merges or deploys under
    integration model A — ADR 0004 §4.
    """
    with executing(REGISTRY["release"]):
        CredentialRequest(kind=kind, realm="k8s:prod")


def test_engine_plumbing_outside_any_node_is_unaffected() -> None:
    """The Flow Engine and the Workbench resolve these; only nodes with a model are refused."""
    CredentialRequest(kind=CredentialKind.DEPLOY, realm="k8s:prod")


def test_the_privileged_set_is_exactly_what_the_invariant_names() -> None:
    """CI, merge, deploy. A drift here silently narrows the control."""
    assert {k.value for k in PRIVILEGED_KINDS} == {"ci.trigger", "scm.merge", "deploy"}


# --- invariant 4: verifiers get a fresh context -------------------------------------------


def test_every_verifier_is_classified_verifier() -> None:
    verifiers = [s for n, s in REGISTRY.items() if n.startswith("verifier.")]
    assert len(verifiers) == 4
    assert all(s.node_class is NodeClass.VERIFIER for s in verifiers)


# --- invariant 5: risk_tier may only be raised --------------------------------------------


def test_tier_is_raised_not_lowered() -> None:
    assert raise_to("low", "high") == "high"
    assert raise_to("high", "low") == "high"
    assert raise_to("medium", "medium") == "medium"


def test_lowering_is_rejected_where_it_would_be_a_defect() -> None:
    assert_not_lowered("low", "high")
    with pytest.raises(RiskTierLowered):
        assert_not_lowered("high", "medium")


def test_gate_depth_follows_tier() -> None:
    assert required_approvals("low") == 0
    assert required_approvals("medium") == 1
    assert required_approvals("high") == 2


# --- invariant 10: agents never write protected paths -------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        ".github/workflows/nested/deep.yml",
        ".github/actions/build/action.yml",
        "Jenkinsfile",
        "charts/app/values.yaml",
        "terraform/main.tf",
        "envs/prod.tfvars",
        "kuwarden.yaml",
        "services/payments/kuwarden.yaml",
        "policy.yaml",
    ],
)
def test_protected_paths_are_denied(path: str) -> None:
    assert ProtectedPaths().matches(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        "src/main.py",
        "README.md",
        "docs/github/workflows.md",
        "charts.py",
        "tests/test_terraform_helpers.py",
    ],
)
def test_ordinary_paths_are_allowed(path: str) -> None:
    assert ProtectedPaths().matches(path) is None


def test_single_star_does_not_cross_a_separator() -> None:
    """`fnmatch` would match here, which is why it is not used."""
    assert ProtectedPaths(patterns=("charts/*",)).matches("charts/app/values.yaml") is None
    assert ProtectedPaths(patterns=("charts/**",)).matches("charts/app/values.yaml") is not None


def test_enforced_protected_paths_match_policy_example() -> None:
    """The enforced copy and the documented one must not drift apart."""
    text = (REPO_ROOT / "docs" / "reference" / "policy.example.yaml").read_text(encoding="utf-8")
    block = re.search(r"^protected_paths:\n((?:\s+-.*\n|\s*#.*\n|\s*\n)*)", text, re.MULTILINE)
    assert block is not None, "protected_paths block not found in policy.example.yaml"
    documented = set(re.findall(r'-\s*"([^"]+)"', block.group(1)))
    assert documented == set(DEFAULT_PROTECTED_PATHS)


# --- the node contract --------------------------------------------------------------------


def test_registry_and_dispatch_agree() -> None:
    assert set(NODES) == set(REGISTRY)


def test_topology_has_the_expected_nodes() -> None:
    assert set(REGISTRY) == {
        "triage",
        "planner",
        "coder",
        "push",
        "build_test",
        "verifier.correctness",
        "verifier.security",
        "verifier.test_evidence",
        "verifier.regression_risk",
        "release",
        "compensate",
        "reporter",
    }



# --- invariant 4: verifiers get a fresh context --------------------------------------------


def test_a_verifier_never_sees_the_coders_reasoning() -> None:
    """Fresh context, enforced rather than asked for.

    `_verify` used to hand each verifier the whole `FlowState`, so the plan the Coder worked
    from, its retry count and the other verifiers' verdicts were one attribute access away.
    A verifier that has seen the author's reasoning is not an independent check of it.
    """
    import uuid as _uuid

    from engine.flows.delivery import _verifier_brief
    from engine.state import ChangePlan, Diff, FileChange, FlowState, Ticket, Verification

    state = FlowState(
        run_id=_uuid.uuid4(),
        root_run_id=_uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="jira", title="t", body="b"),
        policy_commit="0" * 40,
        policy_bundle={"pinned": True},
        plan=ChangePlan(summary="how I decided to do it", steps=["step"]),
        diff=Diff(files=[FileChange(path="src/app.py", added=1, removed=0)]),
        retry_count=3,
        budget_cents_spent=99,
        verifications=[Verification(verifier="security", passed=False)],
    )

    brief = _verifier_brief(state)

    # The author's reasoning, its failed attempts, and the other verdicts.
    assert brief.plan is None, "the Coder's plan is reasoning about this change"
    assert brief.retry_count == 0, "retry_count *is* prior attempts"
    assert brief.verifications == [], "a fan-out, not a vote"
    assert brief.budget_cents_spent == 0

    # What a verifier legitimately needs: the ask, the change, the evidence, the lineage.
    assert brief.ticket == state.ticket
    assert brief.diff == state.diff
    assert brief.run_id == state.run_id
    assert brief.policy_commit == state.policy_commit


def test_the_brief_is_an_allow_list_so_a_new_field_is_invisible_by_default() -> None:
    """The safe direction. Forgetting to name a field shows a verifier *less*, never more."""
    from engine.flows.delivery import VERIFIER_MAY_SEE
    from engine.state import FlowState as _FlowState

    declared = set(_FlowState.__dataclass_fields__)
    assert declared >= VERIFIER_MAY_SEE, "the allow-list names only real fields"
    # The ones that would defeat the purpose must never appear in it.
    assert not (VERIFIER_MAY_SEE & {"plan", "retry_count", "verifications", "approvals", "notes"})


def test_a_verifier_cannot_read_the_coders_reasoning_out_of_a_note() -> None:
    """The allow-list working on a field added after it was written.

    `notes` carries the Planner's full prompt and the Coder's per-attempt account of itself —
    everything invariant 4 removes from `plan` and `retry_count`, in prose. A verifier that
    could read it would be reading the author's reasoning through a different attribute, and
    the redaction above would be decorative.

    This passes because the brief is an allow-list, not because anyone remembered to add
    `notes` to a deny-list. That is the property being tested.
    """
    import uuid as _uuid

    from engine.flows.delivery import _verifier_brief
    from engine.state import FlowState, Ticket

    state = FlowState(
        run_id=_uuid.uuid4(),
        root_run_id=_uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="jira", title="t", body="b"),
        policy_commit="0" * 40,
        policy_bundle={"pinned": True},
        notes={"summary": "I tried three times and weakened a test", "sections": []},
    )

    assert _verifier_brief(state).notes == {}


# --- invariant 12: the sandbox holds no credentials ---------------------------------------
#
# The companion test in `test_sandbox.py` runs a container and reads its environment, which is
# the stronger evidence but needs podman and is skipped without it. This one asserts the same
# property against the argv we construct, so it runs everywhere — including on a CI runner
# with no container runtime. The regression it exists to catch is someone adding an `--env`
# during a debugging session and leaving it there; that edit is invisible to every other test
# in the suite.


class _ArgvOnlyPodman(PodmanSandbox):
    """A `PodmanSandbox` that records the argv it would have run, and runs nothing."""

    def __init__(self) -> None:
        super().__init__(require_full_isolation=False)
        self.argv: list[str] = []

    async def capabilities(self) -> SandboxCapabilities:
        """Fixed, because probing runs a real container.

        The argv is built identically whatever the host enforces, so answering "everything"
        keeps this test independent of podman without weakening what it asserts.
        """
        return SandboxCapabilities(
            cgroup_memory=True,
            cgroup_cpu=True,
            cgroup_pids=True,
            rlimit_memory=True,
            tmpfs_quota=True,
        )

    async def _run(self, argv: list[str], *, timeout_s: int) -> tuple[int, str, str]:
        self.argv = argv
        return 0, "", ""


async def test_the_sandbox_is_never_invoked_with_a_forwarded_environment(
    tmp_path: Path,
) -> None:
    """Exactly one `--env`, and it carries no credential.

    `HOME` is required because the root filesystem is read-only, so the process needs
    somewhere writable. Every other environment-bearing podman flag is a way for a host
    token to reach code a ticket author influenced: `--env-host` forwards the lot, and
    `--env-file` forwards whatever a path happens to contain.
    """
    sandbox = _ArgvOnlyPodman()

    await sandbox.exec(Workspace(root=str(tmp_path)), "toolchain:test", ["true"], ResourceLimits())

    forwarding = [arg for arg in sandbox.argv if arg.startswith(("--env", "-e"))]
    assert forwarding == ["--env=HOME=/tmp"], (
        "invariant 12: the sandbox holds no credentials. Adding an environment flag here "
        "hands a host secret to code written by a model that read a ticket anyone can file."
    )


def test_a_verifier_sees_the_repository_but_still_not_the_coders_reasoning() -> None:
    """The base tree is context; the Coder's thinking is not. Invariant 4 draws that line.

    Verifiers used to see the diff and nothing else, and it made them reject valid work.
    Asked to switch the site theme, the Coder set `data-theme="ocean"` and changed nothing
    else — correctly, because `[data-theme="ocean"]` already existed in `globals.css`. Two
    verifiers blocked it on "globals.css is not among the changed files, so there is no
    evidence those tokens exist". Both reasoned soundly from what they had; neither could
    open the file.

    Reading the repository at a public commit is not seeing anyone's reasoning — it is what
    any reviewer opening the pull request would have. What invariant 4 forbids is unchanged
    and is asserted here alongside it, so a later widening cannot quietly take the plan too.
    """
    repository, _ = repo_context.render(
        {
            "app/globals.css": b'[data-theme="ocean"] { --bg: #123; }\n',
            "app/layout.tsx": b"export default function Layout() {}\n",
        },
        "repository",
    )

    state = FlowState(
        run_id=uuid.uuid4(),
        root_run_id=uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="jira", title="switch theme", body="."),
        policy_commit="0" * 40,
        policy_bundle={},
        plan=ChangePlan(summary="SECRET PLAN", steps=["do not leak me"]),
        retry_count=3,
        proposed_edits=[ProposedEdit(path="app/layout.tsx", content="data-theme='ocean'")],
    )

    prompt = verifiers._prompt(_verifier_brief(state), repository)

    assert '[data-theme="ocean"]' in prompt, (
        "a verifier must be able to check whether something the change refers to exists"
    )
    assert "app/globals.css" in prompt
    # The line that must not move.
    assert "SECRET PLAN" not in prompt
    assert "do not leak me" not in prompt
