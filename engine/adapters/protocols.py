"""One interface, N implementations — the `adapters/` contract from CLAUDE.md.

Azure DevOps, GitHub, GitLab and Jira differ in their REST shapes and in almost nothing that
matters to a node. Where they differ in something that *does* matter — whether the platform
can pause its own deployment and ask us — that difference is named explicitly as a capability
and probed at registration, never assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from engine.state import Ticket

# --- ticket systems -----------------------------------------------------------------------


@dataclass(frozen=True)
class TicketRef:
    system: str
    project: str
    id: str

    @property
    def realm(self) -> str:
        return f"{self.system}:{self.project}"


class TicketAdapter(Protocol):
    """Everything a flow does to a system of record for work.

    `fetch` returns hostile input. Anyone who can file a ticket can write text that a model
    will read as instructions, and no adapter sanitises its way out of that — the defence is
    architectural, not textual.
    """

    async def fetch(self, ref: TicketRef) -> Ticket: ...

    async def comment(self, ref: TicketRef, body: str) -> None: ...

    async def transition(self, ref: TicketRef, state: str) -> None: ...


# --- source control -----------------------------------------------------------------------


@dataclass(frozen=True)
class RepoRef:
    host: str
    org: str
    repo: str
    #: Azure DevOps nests repositories under a project; GitHub and GitLab do not.
    project: str | None = None

    @property
    def realm(self) -> str:
        return f"{self.host}:{self.org}"


@dataclass(frozen=True)
class BranchRef:
    name: str
    commit: str


@dataclass(frozen=True)
class FileEdit:
    path: str
    content: str


@dataclass(frozen=True)
class PullRequest:
    id: str
    url: str
    source_branch: str


class IntegrationModel(StrEnum):
    """Where the control point sits — ADR 0004. Declared, never inferred."""

    KUWARDEN_DEPLOYS = "kuwarden_deploys"
    GATED_MERGE = "gated_merge"
    GATED_DEPLOYMENT = "gated_deployment"


@dataclass(frozen=True)
class ScmCapabilities:
    """What a platform can actually do, as observed rather than as assumed.

    Recorded at registration so that a later argument about which model was achievable has an
    answer that is not somebody's memory.
    """

    #: Model C — the platform pauses its own deployment and asks an external approver.
    deployment_protection: bool = False
    #: Model B — a required status check can block merge.
    required_status_checks: bool = False
    #: Model A — the repository's pipeline can be restricted so that merge does not deploy.
    restrictable_pipeline_triggers: bool = False
    #: Free-form evidence: what was queried and what came back.
    detail: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelVerdict:
    achievable: bool
    reason: str


def validate_integration_model(
    declared: IntegrationModel, capabilities: ScmCapabilities
) -> ModelVerdict:
    """Refuse a model the platform cannot actually support — ADR 0004 §2.

    The adapter may validate the declaration; it may not make it. Detecting the platform and
    choosing on the operator's behalf was rejected: which control point governs a deployment
    is a governance decision, and it has to be declared, reviewed, and visible.
    """
    match declared:
        case IntegrationModel.GATED_DEPLOYMENT:
            if not capabilities.deployment_protection:
                return ModelVerdict(
                    False,
                    "the platform exposes no deployment protection rule, so there is no point "
                    "at which it would pause and ask KuWarden",
                )
        case IntegrationModel.GATED_MERGE:
            if not capabilities.required_status_checks:
                return ModelVerdict(
                    False,
                    "no required status check is available, so merge cannot be gated",
                )
        case IntegrationModel.KUWARDEN_DEPLOYS:
            if not capabilities.restrictable_pipeline_triggers:
                return ModelVerdict(
                    False,
                    "the repository's pipeline cannot be restricted to manual or tag dispatch, "
                    "so merging would deploy alongside KuWarden -- a double deploy, or a race",
                )
    return ModelVerdict(True, "declared model is supported by the platform as probed")


class ScmAdapter(Protocol):
    """Source control, from the Flow Engine's side of the credential boundary.

    Note what is absent: no `merge`, and no `deploy`. Those are separate capabilities under
    separate credentials, resolved after gates pass. A node holding this interface can read
    code and write its own branch, and nothing else.
    """

    async def probe(self, ref: RepoRef) -> ScmCapabilities: ...

    async def default_branch(self, ref: RepoRef) -> BranchRef: ...

    async def push_change(
        self,
        ref: RepoRef,
        base: BranchRef,
        branch: str,
        message: str,
        edits: list[FileEdit],
    ) -> BranchRef: ...

    async def open_pull_request(
        self,
        ref: RepoRef,
        source: str,
        target: str,
        title: str,
        description: str,
    ) -> PullRequest: ...
