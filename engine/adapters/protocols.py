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

    async def ping(self, ref: TicketRef) -> str:
        """Prove the credential reaches this project, and return what it found.

        Deliberately scoped to the *project* rather than to the account. An "am I
        authenticated" check passes with a perfectly good token pointed at a project that does
        not exist, which is the mistake operators actually make — a typo in the project name,
        or the wrong organisation. `ref.id` is unused; a ping needs no ticket.

        Read-only, and cheap enough to run from a button.
        """
        ...

    async def fetch(self, ref: TicketRef) -> Ticket: ...

    async def comment(self, ref: TicketRef, body: str) -> None: ...

    async def comments(self, ref: TicketRef) -> list[str]:
        """Every comment body on the ticket, for callers that must not post twice.

        A ticket API offers no idempotency token, so a caller whose activity retried cannot
        tell a landed write from a lost one without reading back. Bodies rather than a richer
        shape, because the only question asked of them is whether a marker is present.
        """
        ...

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
class TreeLimits:
    """Bounds on how much of a repository is pulled into a sandbox workspace.

    These exist to be **refused**, not silently applied. A Coder editing against a tree that
    was quietly truncated writes code against a repository that does not exist — it deletes
    a call site it cannot see, and the failure surfaces at CI or, worse, in review as a
    change nobody can explain. Every bound here raises rather than trims.
    """

    max_files: int = 4000
    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 64_000_000
    #: Paths never pulled. Compiled artefacts and vendored dependencies are large, are not
    #: what anyone asked the agent to change, and would dominate the model's context.
    exclude: tuple[str, ...] = (
        ".git/**",
        "**/node_modules/**",
        "**/.venv/**",
        "**/venv/**",
        "**/__pycache__/**",
        "**/dist/**",
        "**/build/**",
        "**/target/**",
        "**/*.lock",
    )


@dataclass(frozen=True)
class RepoTree:
    """A repository's contents at one commit.

    Bytes rather than text: a repository contains binaries, and decoding everything as UTF-8
    is how a PNG becomes a UnicodeDecodeError three nodes later.
    """

    commit: str
    files: dict[str, bytes]

    @property
    def total_bytes(self) -> int:
        return sum(len(content) for content in self.files.values())


@dataclass(frozen=True)
class BranchRef:
    name: str
    commit: str


@dataclass(frozen=True)
class FileEdit:
    """One path's new state in a push. `deleted` removes it; `content` is then empty.

    Every adapter must honour `deleted` — dropping it would push a change that silently
    leaves the removed file in place, which reads as a successful push of the wrong tree.
    """

    path: str
    content: str
    deleted: bool = False


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

    async def write_access(self, ref: RepoRef) -> tuple[bool | None, str]:
        """Whether the credential may write a branch, without writing one.

        `None` means the platform offers no cheap way to ask. That is a third answer, not a
        polite `True`: reporting "writable" on a token nobody checked is the overstatement
        this codebase keeps refusing to make.

        Worth its own call because of when the alternative fails. Read access is enough for
        every node up to and including the Coder, so a token missing only the write grant
        produces a full model run — real tokens, real cost — and then a 403 at Push.
        """
        ...

    async def read_tree(
        self, ref: RepoRef, commit: str, limits: TreeLimits | None = None
    ) -> RepoTree:
        """Every file in the repository at `commit`.

        Read by the Flow Engine, which holds the token, and written into a sandbox workspace
        that holds none. The sandbox is handed a directory and never learns where it came
        from.
        """
        ...

    async def push_change(
        self,
        ref: RepoRef,
        base: BranchRef,
        branch: str,
        message: str,
        edits: list[FileEdit],
        parent: str | None = None,
    ) -> BranchRef:
        """Create or fast-forward `branch` with `edits` applied to `base`'s tree.

        `base` is the pinned commit the Coder read, and stays the same for every attempt of a
        run: the resulting tree is always *base plus the current edits*, never the previous
        attempt's tree plus this one's. Otherwise a file changed in attempt 1 and left alone
        in attempt 2 would survive on the branch while being absent from the diff Build & Test
        graded, and the branch CI runs on would not be the change anyone reviewed.

        `parent` is the commit the new one is parented on — the branch's own tip once this run
        has pushed to it, so the branch reads as a history of attempts. It defaults to `base`,
        which is the first push.

        **Idempotent on `message`.** Temporal retries an activity whose effect landed but
        whose acknowledgement was lost; an implementation that blindly committed would add a
        duplicate commit every retry. The message carries run id and attempt, so it names one
        intended push and no other.

        Never force-updates. A branch that has moved to something this run did not write is
        refused, because overwriting it destroys evidence rather than resolving a conflict.
        """
        ...

    async def delete_branch(self, ref: RepoRef, branch: str) -> bool:
        """Remove a branch this run created. `False` if it was already gone.

        Idempotent, because compensation is retried: a branch that has already been deleted
        is the state the caller wanted, not an error.

        Only ever called for a branch KuWarden pushed. There is no method here for deleting
        an arbitrary ref, and that absence is the point — the SCM interface grants "write my
        own branch", and being able to remove somebody else's is not part of it.
        """
        ...

    async def open_pull_request(
        self,
        ref: RepoRef,
        source: str,
        target: str,
        title: str,
        description: str,
    ) -> PullRequest: ...

    async def preview_url(self, ref: RepoRef, commit: str) -> str | None:
        """A running deployment of this exact commit, if the platform published one.

        The one dimension nothing in this system anchors is **whether the change does what was
        asked**. Every gate verifies form: lint, types, the build, a diff read by four models.
        Ticket 50 passed all of them and shipped a feature that did not work, and the defect
        took thirty seconds to find by opening the page and clicking.

        So this is not a verdict and must never become one — a preview that loads proves
        nothing. It is a link, put in front of the approver, so that thirty seconds is
        available to them at the moment they decide. Per ADR 0002 the only figure that shows
        this product saved anyone work is human minutes per run; thirty seconds spent here is
        a good trade against a broken deployment.

        `None` means the platform published nothing for this commit, which is the ordinary
        case for a repository with no preview environment. It is not an error and must not be
        reported as one.
        """
        return None

    async def merge_pull_request(self, ref: RepoRef, number: str, commit: str) -> str:
        """Merge an open pull request, and return the resulting commit on the target branch.

        The control point of ADR 0004 model B, and the only method on this interface that
        writes to a branch KuWarden does not own. It requires `SCM_MERGE`, which
        `PRIVILEGED_KINDS` forbids any model-bearing node from holding (invariant 2) — so it
        is reachable from a deterministic node and nowhere else.

        `commit` is the head the caller graded, passed so the implementation can refuse if the
        branch has moved since. Merging a revision nobody verified is the failure this
        argument exists to prevent.
        """
        ...
