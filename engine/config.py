"""`kuwarden.yaml` — an application's declaration of how its changes are delivered.

Distinct from `policy.yaml`, which describes the platform deployment. An application cannot
grant itself capabilities: this file may only *select from* what `policy.yaml` already
permits — ADR 0003 §1. It is also a protected path, so an agent may never write it.

The example in ARCHITECTURE.md §2.5 predates ADR 0002 and ADR 0004 and describes a linear
pipeline with uniform approval gates. This is the schema the ADRs actually imply: no
pipeline key at all, because the topology is fixed and is not per-application configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engine.adapters.llm import Provider
from engine.adapters.protocols import IntegrationModel, RepoRef, TicketRef
from engine.errors import KuWardenError
from engine.state import RISK_TIER_ORDER, RiskTier


class ConfigError(KuWardenError):
    """`kuwarden.yaml` is absent, malformed, or declares something unsupportable."""


@dataclass(frozen=True)
class RepoConfig:
    name: str
    provider: str  # "github" | "azure_repos"
    org: str
    repo: str
    project: str | None = None
    path: str = "."

    def ref(self) -> RepoRef:
        host = "github.com" if self.provider == "github" else "dev.azure.com"
        return RepoRef(host=host, org=self.org, repo=self.repo, project=self.project)


@dataclass(frozen=True)
class TriggerConfig:
    provider: str  # "jira" | "azure_devops"
    project: str
    site: str | None = None
    account_email: str | None = None
    organisation: str | None = None
    label: str | None = None
    #: The workflow state a ticket must be in for a run to be admitted — "Ready for Agent".
    #: `None` means state is not checked.
    #:
    #: This is the trigger that scales. A ticket *save* fires on every field change — a
    #: reassignment, a typo fix, a tag — and starting an agent run on each is someone else's
    #: model budget. Moving a ticket into a named state is a deliberate act, so admission
    #: reads an intention rather than inferring one from activity.
    ready_state: str | None = None
    max_story_points: int | None = None
    story_points_field: str | None = None

    def ref(self, ticket_id: str) -> TicketRef:
        return TicketRef(system=self.provider, project=self.project, id=ticket_id)


#: The four angles, named once. `engine.flows.delivery.VERIFIERS` prefixes these with
#: `verifier.`; keeping the bare names here is what lets configuration read naturally.
ALL_VERIFIERS: tuple[str, ...] = ("correctness", "security", "test_evidence", "regression_risk")


@dataclass(frozen=True)
class VerificationConfig:
    """Which verifiers may stop a change, and which only advise.

    Every verifier always runs. Turning one off makes it **advisory**: it still reads the diff,
    still records its findings, and still reaches the audit trail — it simply cannot abort the
    run. Skipping it outright would save a model call and destroy the evidence, which for a
    product whose value is the record is the wrong trade.

    Default is every verifier blocking. A gate that arrives switched off is a gate nobody
    decided to have.
    """

    #: Verifier ids — bare, without the `verifier.` prefix — that may block.
    blocking: frozenset[str] = field(default_factory=lambda: frozenset(ALL_VERIFIERS))

    def advisory(self) -> tuple[str, ...]:
        """The ones that cannot block, in a stable order for the record."""
        return tuple(sorted(set(ALL_VERIFIERS) - self.blocking))


@dataclass(frozen=True)
class RiskConfig:
    """Rules-first tiering. An LLM may raise a tier from here; it may never lower one."""

    high_paths: list[str] = field(default_factory=list)
    medium_paths: list[str] = field(default_factory=list)
    high_labels: list[str] = field(default_factory=list)
    #: Above this many changed files, one approver is required. Size is the cheapest honest
    #: proxy for "somebody should look at this" — it needs no understanding of the change.
    medium_changed_files: int | None = None
    #: Above this many changed files, the change stops being small whatever it touched.
    high_changed_files: int | None = None


@dataclass(frozen=True)
class SandboxConfig:
    """Where and how the Coder's inner loop executes — ADR 0005.

    `require_full_isolation` defaults to **False** during the testing phase, so a developer
    host that cannot enforce cgroup limits still runs. Production should set it True.

    Defaulting an isolation control to off is normally how a control never gets turned on, so
    the degradation is made expensive to ignore instead of expensive to hit: it is reported
    by `GET /api/sandbox`, banner-level in the Workbench on every page, and logged at WARNING
    on every execution. The fact that a run executed under weakened isolation is a property
    of that run, not a transient UI state.
    """

    #: Image reference. Dependencies are baked in; there is no egress to install any.
    #:
    #: **No default, deliberately.** This used to default to the Python image and
    #: `test_command` to `pytest -q`, which meant an application that declared no `sandbox:`
    #: block was graded by pytest whatever language it was written in. For a TypeScript
    #: project that collects no tests, pytest exits 5 and the obvious workaround
    #: (`test $? -le 5`) turns every change into a pass — a green Build & Test that verified
    #: nothing, with nothing in the record saying so.
    #:
    #: That is invariant 11's failure aimed at the reality anchor: overstating what was
    #: checked is manufacturing evidence, and the fix is the same one `control_mode` gets —
    #: never inferred, never defaulted. An application that has not said how to verify a
    #: change cannot have one verified.
    toolchain_image: str
    #: What Build & Test runs. Its exit code is the reality anchor -- ADR 0001. No default,
    #: for the reason given on `toolchain_image`.
    #:
    #: Declared here, above the defaulted fields, because a dataclass requires it — which is
    #: a useful accident: the two things an application must state about verification are the
    #: first two things in this class.
    test_command: list[str]
    #: Limits below all have defaults. They are safe to guess in a way the two fields above
    #: are not: too little memory fails loudly and identically for every change, whereas the
    #: wrong test command passes quietly and means nothing.
    memory_mb: int = 2048
    cpus: float = 2.0
    pids: int = 256
    timeout_s: int = 600
    tmp_mb: int = 512
    require_full_isolation: bool = False


@dataclass(frozen=True)
class CiConfig:
    """Where the independent verdict comes from — invariant 3, and ADR 0007.

    Absent is legal and means the sandbox verdict stands, carrying its caveat. That is the
    honest default: a repository with no pipeline cannot be made to have one by configuration,
    and refusing to run there would trade a labelled weakness for no product.

    Two waits, because two different things go wrong. `grace_s` bounds *nothing has appeared
    yet* — a pipeline takes seconds to be created after a push. `wait_s` bounds *it appeared
    and is still going*.
    """

    provider: str  # "github_actions"
    wait_s: int = 900
    poll_s: int = 15
    grace_s: int = 90
    #: Which workflows gate. Empty means all of them. Names match the workflow's display name
    #: or its definition path, both exactly.
    required_workflows: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NodeModel:
    model: str
    effort: str = "high"
    max_tokens: int = 8192


@dataclass(frozen=True)
class LLMConfig:
    """Which model each node uses. Never a default in engine code.

    The API key is deliberately absent: it is resolved by the credential broker, so a
    kuwarden.yaml committed to an application repository never carries one.
    """

    provider: Provider
    per_node: dict[str, NodeModel] = field(default_factory=dict)
    base_url: str | None = None

    def for_node(self, node_id: str) -> NodeModel:
        if node_id in self.per_node:
            return self.per_node[node_id]
        # Verifiers share a setting unless one is named individually.
        if node_id.startswith("verifier.") and "verifiers" in self.per_node:
            return self.per_node["verifiers"]
        raise ConfigError(f"llm has no model configured for node {node_id!r}")


@dataclass(frozen=True)
class AutoMergeConfig:
    """When KuWarden may merge its own pull request instead of leaving it for a human.

    Only meaningful under `gated_merge`, which ADR 0004 defines as the model where KuWarden
    *holds merge authority* — this is that authority being exercised rather than a new power.
    Disabled unless declared, because "the agent may write to the default branch" is not a
    setting anyone should acquire by upgrading.

    `require_external_anchor` is the one to think hardest about. False means a change can
    reach the default branch graded only by KuWarden's own sandbox — the same system that
    wrote it. That is the arrangement this product exists to argue against, so it is a line
    someone has to type.
    """

    enabled: bool = False
    #: Merge only at or below this tier. `low` means no human approval was required anyway.
    max_risk_tier: RiskTier = "low"
    #: Above this many changed files, a human looks at it. None disables the check.
    max_files_changed: int | None = None
    #: Require `CIResult.source == "ci"` — a pipeline KuWarden does not control.
    require_external_anchor: bool = True


@dataclass(frozen=True)
class AppConfig:
    name: str
    repos: list[RepoConfig]
    triggers: list[TriggerConfig]
    integration_model: IntegrationModel
    #: Required, and required *here* rather than defaulted, because an application with no
    #: sandbox declaration has no way to have a change verified — see `SandboxConfig`.
    sandbox: SandboxConfig
    auto_merge: AutoMergeConfig = field(default_factory=lambda: AutoMergeConfig())
    toolchain_id: str = "none"
    risk: RiskConfig = field(default_factory=RiskConfig)
    llm: LLMConfig | None = None
    ci: CiConfig | None = None
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    budget_cents_per_run: int = 0
    max_coder_retries: int = 3
    default_risk_tier: RiskTier = "low"

    @property
    def primary(self) -> RepoConfig:
        return self.repos[0]


def load(path: str | Path) -> AppConfig:
    file = Path(path)
    if not file.exists():
        raise ConfigError(f"no kuwarden.yaml at {file}")
    return parse(file.read_text(encoding="utf-8"))


def parse(text: str) -> AppConfig:
    """`safe_load`, never `load`.

    This file is read from an application repository, which means it is written by whoever
    can open a pull request there. `yaml.load` constructs arbitrary Python objects.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"kuwarden.yaml is not valid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError("kuwarden.yaml must be a mapping")

    version = raw.get("version")
    if version != 1:
        raise ConfigError(f"unsupported kuwarden.yaml version {version!r}; expected 1")

    app = _mapping(raw, "app")
    name = app.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("app.name is required")

    repos = [_repo(entry, i) for i, entry in enumerate(_sequence(raw, "workspace", "repos"))]
    if not repos:
        raise ConfigError("workspace.repos must contain at least one repository")

    triggers = [_trigger(entry, i) for i, entry in enumerate(raw.get("triggers") or [])]

    delivery = _mapping(raw, "delivery")
    declared = delivery.get("integration_model")
    # Never inferred and never defaulted: which control point governs a deployment is a
    # governance decision -- ADR 0004. An absent value is an error, not model C.
    if declared is None:
        raise ConfigError(
            "delivery.integration_model must be declared explicitly "
            f"(one of {', '.join(m.value for m in IntegrationModel)})"
        )
    try:
        integration_model = IntegrationModel(declared)
    except ValueError:
        raise ConfigError(f"unknown integration_model {declared!r}") from None

    auto_merge = _auto_merge(delivery.get("auto_merge"), integration_model)
    verification = _verification(raw.get("verification"))

    risk_raw = raw.get("risk") or {}
    budgets = raw.get("budgets") or {}
    llm = _llm(raw.get("llm"))
    sandbox = _sandbox(raw.get("sandbox"))
    ci = _ci(raw.get("ci"))

    return AppConfig(
        auto_merge=auto_merge,
        verification=verification,
        name=name,
        repos=repos,
        triggers=triggers,
        integration_model=integration_model,
        toolchain_id=str(raw.get("toolchain", {}).get("id", "none")),
        llm=llm,
        sandbox=sandbox,
        ci=ci,
        risk=RiskConfig(
            high_paths=[str(p) for p in risk_raw.get("high_paths", [])],
            medium_paths=[str(p) for p in risk_raw.get("medium_paths", [])],
            high_labels=[str(label) for label in risk_raw.get("high_labels", [])],
            medium_changed_files=risk_raw.get("medium_changed_files"),
            high_changed_files=risk_raw.get("high_changed_files"),
        ),
        budget_cents_per_run=int(budgets.get("cents_per_run", 0)),
        max_coder_retries=int(raw.get("flow", {}).get("max_coder_retries", 3)),
    )


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _sequence(raw: dict[str, Any], outer: str, inner: str) -> list[Any]:
    value = _mapping(raw, outer).get(inner)
    if not isinstance(value, list):
        raise ConfigError(f"{outer}.{inner} must be a list")
    return value


def _repo(entry: Any, index: int) -> RepoConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"workspace.repos[{index}] must be a mapping")
    provider = str(entry.get("provider", ""))
    if provider not in {"github", "azure_repos"}:
        raise ConfigError(
            f"workspace.repos[{index}].provider must be github or azure_repos, got {provider!r}"
        )
    if provider == "azure_repos" and not entry.get("project"):
        raise ConfigError(f"workspace.repos[{index}] is azure_repos and needs a project")
    for required in ("name", "org", "repo"):
        if not entry.get(required):
            raise ConfigError(f"workspace.repos[{index}].{required} is required")
    return RepoConfig(
        name=str(entry["name"]),
        provider=provider,
        org=str(entry["org"]),
        repo=str(entry["repo"]),
        project=str(entry["project"]) if entry.get("project") else None,
        path=str(entry.get("path", ".")),
    )


def _trigger(entry: Any, index: int) -> TriggerConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"triggers[{index}] must be a mapping")
    provider = str(entry.get("provider", ""))
    if provider not in {"jira", "azure_devops"}:
        raise ConfigError(
            f"triggers[{index}].provider must be jira or azure_devops, got {provider!r}"
        )
    if provider == "jira" and not entry.get("site"):
        raise ConfigError(f"triggers[{index}] is jira and needs a site url")
    if provider == "azure_devops" and not entry.get("organisation"):
        raise ConfigError(f"triggers[{index}] is azure_devops and needs an organisation")
    if not entry.get("project"):
        raise ConfigError(f"triggers[{index}].project is required")
    return TriggerConfig(
        provider=provider,
        project=str(entry["project"]),
        site=str(entry["site"]) if entry.get("site") else None,
        account_email=str(entry["account_email"]) if entry.get("account_email") else None,
        organisation=str(entry["organisation"]) if entry.get("organisation") else None,
        label=str(entry["label"]) if entry.get("label") else None,
        ready_state=str(entry["ready_state"]) if entry.get("ready_state") else None,
        max_story_points=entry.get("max_story_points"),
        story_points_field=(
            str(entry["story_points_field"]) if entry.get("story_points_field") else None
        ),
    )


def _llm(raw: Any) -> LLMConfig | None:
    """Absent is legal: the walking skeleton runs with every node empty, by design."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("llm must be a mapping")

    declared = raw.get("provider")
    if declared is None:
        raise ConfigError(
            "llm.provider must be declared explicitly "
            f"(one of {', '.join(p.value for p in Provider)})"
        )
    try:
        provider = Provider(declared)
    except ValueError:
        raise ConfigError(f"unknown llm.provider {declared!r}") from None

    for forbidden in ("api_key", "key", "token", "secret"):
        if forbidden in raw:
            # Fail loudly rather than ignoring it. A key written here is a key in the
            # application's git history, and this file is world-readable to that repo.
            raise ConfigError(
                f"llm.{forbidden} must not appear in kuwarden.yaml; credentials are resolved "
                "by the broker from the environment or the secret store"
            )

    per_node: dict[str, NodeModel] = {}
    for node_id, entry in raw.items():
        if node_id in {"provider", "base_url"}:
            continue
        if not isinstance(entry, dict) or not entry.get("model"):
            raise ConfigError(f"llm.{node_id} needs a model")
        per_node[node_id] = NodeModel(
            model=str(entry["model"]),
            effort=str(entry.get("effort", "high")),
            max_tokens=int(entry.get("max_tokens", 8192)),
        )

    return LLMConfig(
        provider=provider,
        per_node=per_node,
        base_url=str(raw["base_url"]) if raw.get("base_url") else None,
    )


def _verification(raw: Any) -> VerificationConfig:
    """Parse `verification.verifiers`, refusing a name that is not a verifier.

    A typo would otherwise disable nothing and read as though it had — the operator sees
    `test_evidince: false` in their own file and believes the gate is off, or on, and is wrong
    either way. Naming the four is cheap; guessing is not.
    """
    if raw is None:
        return VerificationConfig()
    if not isinstance(raw, dict):
        raise ConfigError("verification must be a mapping")

    declared = raw.get("verifiers")
    if declared is None:
        return VerificationConfig()
    if not isinstance(declared, dict):
        raise ConfigError("verification.verifiers must be a mapping of name to true/false")

    unknown = set(declared) - set(ALL_VERIFIERS)
    if unknown:
        raise ConfigError(
            f"unknown verifier(s) {', '.join(sorted(unknown))}; "
            f"expected any of {', '.join(ALL_VERIFIERS)}"
        )
    # Absent means blocking. Only an explicit `false` turns one advisory, so a partial mapping
    # cannot quietly disable the ones it forgot to mention.
    blocking = {name for name in ALL_VERIFIERS if declared.get(name, True)}
    return VerificationConfig(blocking=frozenset(blocking))


def _auto_merge(raw: Any, model: IntegrationModel) -> AutoMergeConfig:
    """Parse `delivery.auto_merge`, refusing combinations that would be a lie.

    Declaring it under any model but `gated_merge` is an error rather than an ignored key:
    ADR 0004 gives merge authority to model B alone, and a config that appears to grant it
    under model C would describe a control the deployment does not have.
    """
    if raw is None:
        return AutoMergeConfig()
    if not isinstance(raw, dict):
        raise ConfigError("delivery.auto_merge must be a mapping")

    enabled = bool(raw.get("enabled", False))
    if enabled and model is not IntegrationModel.GATED_MERGE:
        raise ConfigError(
            "delivery.auto_merge is only available under integration_model: gated_merge — "
            f"this application declares {model.value}, where KuWarden holds no merge authority "
            "(ADR 0004)"
        )

    tier = raw.get("max_risk_tier", "low")
    if tier not in RISK_TIER_ORDER:
        raise ConfigError(
            f"unknown auto_merge.max_risk_tier {tier!r}; "
            f"expected one of {', '.join(RISK_TIER_ORDER)}"
        )

    files = raw.get("max_files_changed")
    if files is not None and (not isinstance(files, int) or files < 1):
        raise ConfigError("auto_merge.max_files_changed must be a positive integer, or absent")

    return AutoMergeConfig(
        enabled=enabled,
        max_risk_tier=tier,
        max_files_changed=files,
        # Defaults to True, so leaving it out keeps the anchor requirement rather than
        # silently dropping it.
        require_external_anchor=bool(raw.get("require_external_anchor", True)),
    )


def _ci(raw: Any) -> CiConfig | None:
    """Absent is legal: it means no independent anchor, which is stated rather than hidden."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("ci must be a mapping")

    provider = str(raw.get("provider", ""))
    if provider not in {"github_actions"}:
        raise ConfigError(f"ci.provider must be github_actions, got {provider!r}")

    required = raw.get("required_workflows") or []
    if not isinstance(required, list):
        raise ConfigError("ci.required_workflows must be a list of workflow names or paths")

    poll_s = int(raw.get("poll_s", 15))
    if poll_s < 1:
        # A zero here would spin the poll loop against someone else's API without ever
        # advancing the elapsed counter, which is an infinite loop wearing a timeout.
        raise ConfigError("ci.poll_s must be at least 1 second")

    return CiConfig(
        provider=provider,
        wait_s=int(raw.get("wait_s", 900)),
        poll_s=poll_s,
        grace_s=int(raw.get("grace_s", 90)),
        required_workflows=[str(name) for name in required],
    )


#: Said whenever an application has not declared how its changes are to be verified. One
#: message, so the API, the worker and the CLI all refuse in the same words.
_NO_TOOLCHAIN = (
    "declares no {field}, so there is nothing to grade a change with. KuWarden will "
    "not guess: it used to default to a Python image running `pytest -q`, which meant a "
    "repository in any other language got a Build & Test verdict that had verified nothing. "
    "Build an image from this application's own dependency manifest — `uv run python -m "
    "engine.sandbox build-app --recipe <ecosystem> --name <app> --manifest <checkout>` — then "
    "declare `sandbox.toolchain_image` and `sandbox.test_command`."
)


def _sandbox(raw: Any) -> SandboxConfig:
    """Parse the `sandbox:` block. Absent is an error, not a default.

    Both `toolchain_image` and `test_command` must be stated. See `SandboxConfig` for why an
    omitted one cannot be filled in: a guessed verdict is indistinguishable from a real one
    in the record, which is the one property this product cannot afford to lose.
    """
    if raw is None:
        raise ConfigError(_NO_TOOLCHAIN.format(field="sandbox: section"))
    if not isinstance(raw, dict):
        raise ConfigError("sandbox must be a mapping")

    limits = raw.get("limits") or {}
    command = raw.get("test_command")
    if command is None:
        raise ConfigError(_NO_TOOLCHAIN.format(field="sandbox.test_command"))
    if not isinstance(command, list):
        raise ConfigError("sandbox.test_command must be a list of arguments, not a string")
    image = raw.get("toolchain_image")
    if not image:
        raise ConfigError(_NO_TOOLCHAIN.format(field="sandbox.toolchain_image"))

    return SandboxConfig(
        toolchain_image=str(image),
        memory_mb=int(limits.get("memory_mb", 2048)),
        cpus=float(limits.get("cpus", 2.0)),
        pids=int(limits.get("pids", 256)),
        timeout_s=int(limits.get("timeout_s", 600)),
        tmp_mb=int(limits.get("tmp_mb", 512)),
        require_full_isolation=bool(raw.get("require_full_isolation", False)),
        test_command=[str(part) for part in command],
    )
