"""GitHub.

`push_change` goes through the Git Data API — blobs, tree, commit, ref — rather than the
Contents API. The Contents API writes one file per request, which for a multi-file change
produces one commit per file: a diff no reviewer asked for, and a `changed_files` set that
differs per commit. The Git Data path is more calls and one commit.

No clone and no git binary, so the Flow Engine writes a branch without ever materialising the
repository in a process that holds a credential.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from engine.adapters.credentials import (
    CredentialBroker,
    CredentialKind,
    CredentialRequest,
)
from engine.adapters.http import RestClient, bearer_auth_header
from engine.adapters.protocols import (
    BranchRef,
    FileEdit,
    PullRequest,
    RepoRef,
    RepoTree,
    ScmCapabilities,
    TreeLimits,
)
from engine.adapters.scm.tree import (
    TreeTooLarge,
    check_file_count,
    check_file_size,
    excluded,
    finish,
)
from engine.errors import AdapterError, NotFound, PermissionDenied

API_VERSION = "2022-11-28"


class GitHubScm:
    """Implements `ScmAdapter` for GitHub.

    As with every SCM adapter here: no merge, no deploy. Those are separate capabilities
    under separate credentials, resolved by the Flow Engine after gates pass.
    """

    def __init__(
        self,
        broker: CredentialBroker,
        *,
        base_url: str = "https://api.github.com",
        web_url: str = "https://github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._broker = broker
        self._base_url = base_url.rstrip("/")
        self._web_url = web_url.rstrip("/")
        self._transport = transport

    async def _client(self, ref: RepoRef, kind: CredentialKind) -> RestClient:
        token = await self._broker.resolve(CredentialRequest(kind=kind, realm=ref.realm))
        return RestClient(
            base_url=self._base_url,
            auth_header=bearer_auth_header(token),
            transport=self._transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )

    def _repo(self, ref: RepoRef) -> str:
        return f"/repos/{ref.org}/{ref.repo}"

    async def probe(self, ref: RepoRef) -> ScmCapabilities:
        """ADR 0004 §2 — ask, do not assume.

        Two things here are easy to overstate and are deliberately not:

        **An environments *endpoint* is not a deployment gate.** A repository with no
        environments answers 200 with an empty list. Treating "the endpoint replied" as
        "model C is achievable" would declare a control point over a deployment that has no
        environment to pause, which is precisely the kind of claim ADR 0004 §2 exists to stop.
        So the count is what counts.

        **403 and 404 on branch protection are different facts.** A 404 is genuinely
        ambiguous — no rule, or a token without admin scope, and the API will not say which.
        A 403 is not ambiguous at all: the token lacks the permission, and saying "a 404 here
        means…" to someone looking at a 403 sends them to check the wrong thing.
        """
        detail: dict[str, str] = {}

        environments = await self._environment_count(ref, detail)
        default = await self.default_branch(ref)
        protection = await self._probe_endpoint(
            ref,
            f"{self._repo(ref)}/branches/{default.name}/protection",
            detail,
            "branch_protection",
        )
        detail["workflow_triggers"] = (
            "not probed - requires operator attestation that merge does not deploy"
        )

        return ScmCapabilities(
            deployment_protection=environments > 0,
            required_status_checks=protection,
            restrictable_pipeline_triggers=False,
            detail=detail,
        )

    async def _environment_count(self, ref: RepoRef, detail: dict[str, str]) -> int:
        """How many deployment environments exist. Zero means there is nothing to gate."""
        try:
            async with await self._client(ref, CredentialKind.SCM_READ) as client:
                payload: Any = await client.get(f"{self._repo(ref)}/environments")
        except AdapterError as exc:
            detail["environments"] = f"unavailable: {exc}"
            return 0
        count = int(payload.get("total_count", 0)) if isinstance(payload, dict) else 0
        detail["environments"] = (
            f"{count} configured"
            if count
            else "endpoint reachable, but no environment is configured - there is no "
            "deployment for a protection rule to pause"
        )
        return count

    async def _probe_endpoint(
        self, ref: RepoRef, path: str, detail: dict[str, str], key: str
    ) -> bool:
        try:
            async with await self._client(ref, CredentialKind.SCM_READ) as client:
                await client.get(path)
        except NotFound as exc:
            # Genuinely ambiguous, and the API will not disambiguate it. Recorded as the
            # ambiguity it is rather than resolved by guesswork.
            detail[key] = (
                f"unavailable: {exc} - a 404 means either no rule is configured or the token "
                "lacks admin scope; GitHub does not distinguish them"
            )
            return False
        except AdapterError as exc:
            # A 403 says exactly what is wrong: the token may not read this. Pointing the
            # reader at the 404 ambiguity here would send them to check the wrong thing.
            detail[key] = f"unavailable: {exc}"
            return False
        detail[key] = "available"
        return True

    async def default_branch(self, ref: RepoRef) -> BranchRef:
        async with await self._client(ref, CredentialKind.SCM_READ) as client:
            repo: Any = await client.get(self._repo(ref))
            if not isinstance(repo, dict) or "default_branch" not in repo:
                raise AdapterError(f"repository {ref.org}/{ref.repo} returned no default branch")
            name = str(repo["default_branch"])

            try:
                head: Any = await client.get(f"{self._repo(ref)}/git/ref/heads/{name}")
            except NotFound:
                # A repository with no commits still reports a `default_branch`, but the ref
                # does not exist. Left as a bare 404 this reads like a bad token or a wrong
                # repository name, and the operator debugs the wrong thing — so it is named.
                raise AdapterError(
                    f"{ref.org}/{ref.repo} has no commit on {name}: the repository is empty. "
                    "KuWarden pins a base commit before it edits anything, so there must be "
                    "at least one commit to branch from."
                ) from None
        sha = head.get("object", {}).get("sha") if isinstance(head, dict) else None
        if not sha:
            raise AdapterError(f"branch {name} of {ref.repo} resolved to no commit")
        return BranchRef(name=name, commit=str(sha))

    async def write_access(self, ref: RepoRef) -> tuple[bool | None, str]:
        """A useful *negative*, and an honest "unknown" otherwise.

        **`permissions` is the account's role on the repository, not the token's grant.** A
        fine-grained PAT may be scoped far narrower than the account that issued it, so a
        repository owner sees `push: true` here with a token that cannot create a blob. This
        was briefly implemented as `push → writable` and reported "may write a branch" for a
        token that then failed with 403 at Push — the exact overstatement this codebase spends
        its time refusing, so the trap is written down rather than just fixed.

        `push: false` is still worth having: it is definitive, and it catches the read-only
        collaborator and the wrong-account cases. GitHub offers no cheap read that reveals a
        fine-grained token's own grants, so anything else is `None`.
        """
        try:
            async with await self._client(ref, CredentialKind.SCM_READ) as client:
                repo: Any = await client.get(self._repo(ref))
        except AdapterError as exc:
            return False, str(exc)
        permissions = repo.get("permissions") if isinstance(repo, dict) else None
        if not isinstance(permissions, dict):
            return None, "GitHub returned no permissions block"
        if not permissions.get("push"):
            granted = ", ".join(sorted(k for k, v in permissions.items() if v)) or "none"
            return False, (
                f"this account has no push role on the repository (granted: {granted})"
            )
        return None, (
            "not verifiable without writing. GitHub reports the account's role, not the "
            "token's grant, so a fine-grained token scoped to Contents: Read looks identical "
            "here. Confirm Contents: Read and write on the token itself"
        )

    async def read_tree(
        self, ref: RepoRef, commit: str, limits: TreeLimits | None = None
    ) -> RepoTree:
        """Every file at `commit`, via the Git Data API.

        One call lists the tree; one call per blob fetches content. That is N+1 requests,
        which is why `max_files` exists — the alternative, a tarball, arrives as a stream we
        would have to unpack and offers no way to skip excluded paths before downloading
        them.
        """
        bounds = limits or TreeLimits()

        async with await self._client(ref, CredentialKind.SCM_READ) as client:
            listing: Any = await client.get(
                f"{self._repo(ref)}/git/trees/{commit}", params={"recursive": "1"}
            )
            if not isinstance(listing, dict):
                raise AdapterError(f"unexpected tree payload for {commit}")

            # GitHub silently truncates a tree it considers too large. Accepting that would
            # hand the Coder a repository missing arbitrary files, with nothing to indicate
            # which -- the exact silent-partial-success this codebase keeps being bitten by.
            if listing.get("truncated"):
                raise TreeTooLarge(
                    f"GitHub truncated the tree for {ref.org}/{ref.repo} at {commit[:8]}; "
                    "the repository is too large to read in one request and a partial tree "
                    "must not reach a Coder"
                )

            blobs = [
                entry
                for entry in listing.get("tree", [])
                if entry.get("type") == "blob" and not excluded(str(entry["path"]), bounds)
            ]
            check_file_count(len(blobs), bounds, f"{ref.org}/{ref.repo}")

            files: dict[str, bytes] = {}
            for entry in blobs:
                path = str(entry["path"])
                check_file_size(path, int(entry.get("size", 0)), bounds)
                blob: Any = await client.get(f"{self._repo(ref)}/git/blobs/{entry['sha']}")
                files[path] = base64.b64decode(blob.get("content", ""))

        return finish(commit, files, bounds, f"{ref.org}/{ref.repo}")

    async def push_change(
        self,
        ref: RepoRef,
        base: BranchRef,
        branch: str,
        message: str,
        edits: list[FileEdit],
        parent: str | None = None,
    ) -> BranchRef:
        """Create or fast-forward `branch`. See `ScmAdapter.push_change` for the contract."""
        if not edits:
            raise AdapterError("refusing to push an empty change")

        try:
            return await self._push(ref, base, branch, message, edits, parent)
        except PermissionDenied as exc:
            # GitHub answers "Resource not accessible by personal access token" and stops
            # there. True, and it leaves the operator guessing which of a dozen toggles it
            # meant — so the grant is named. The read/write asymmetry is called out because
            # it is what makes this confusing: Contents: Read is enough for the Coder to
            # fetch the tree, so every earlier node succeeds and only the push fails.
            raise PermissionDenied(
                f"{exc}\n\nThis needs Contents: Read and write on "
                f"{ref.org}/{ref.repo}. A token with Contents: Read can fetch the tree — "
                "which is why the earlier nodes succeeded — but cannot write a branch."
            ) from None

    async def _push(
        self,
        ref: RepoRef,
        base: BranchRef,
        branch: str,
        message: str,
        edits: list[FileEdit],
        parent: str | None,
    ) -> BranchRef:
        async with await self._client(ref, CredentialKind.SCM_WRITE_BRANCH) as client:
            existing = await self._branch_tip(client, ref, branch)
            if existing is not None:
                # The idempotency key. A retried activity whose push already landed finds its
                # own message on the tip and returns, rather than committing it twice.
                if await self._commit_message(client, ref, existing.commit) == message:
                    return existing
                expected = parent or base.commit
                if existing.commit != expected:
                    raise AdapterError(
                        f"branch {branch} is at {existing.commit[:8]}, not the "
                        f"{expected[:8]} this push was built on; refusing to overwrite work "
                        "this run did not do"
                    )

            base_commit: Any = await client.get(f"{self._repo(ref)}/git/commits/{base.commit}")
            base_tree = (
                base_commit.get("tree", {}).get("sha")
                if isinstance(base_commit, dict)
                else None
            )
            if not base_tree:
                raise AdapterError(f"commit {base.commit} has no tree")

            tree_entries: list[dict[str, Any]] = []
            for edit in edits:
                if edit.deleted:
                    # A null `sha` against `base_tree` is how the trees API removes a path.
                    # No blob is created — there is no content to store, and posting an empty
                    # one would write an empty file rather than delete it.
                    tree_entries.append(
                        {
                            "path": edit.path.lstrip("/"),
                            "mode": "100644",
                            "type": "blob",
                            "sha": None,
                        }
                    )
                    continue
                blob: Any = await client.post(
                    f"{self._repo(ref)}/git/blobs",
                    json={
                        "content": base64.b64encode(edit.content.encode()).decode(),
                        "encoding": "base64",
                    },
                )
                tree_entries.append(
                    {
                        "path": edit.path.lstrip("/"),
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob["sha"],
                    }
                )

            tree: Any = await client.post(
                f"{self._repo(ref)}/git/trees",
                json={"base_tree": base_tree, "tree": tree_entries},
            )
            commit: Any = await client.post(
                f"{self._repo(ref)}/git/commits",
                # The tree comes from `base`, the parent from the branch tip. Parenting on
                # the tip keeps the attempts in history; taking the tree from `base` keeps
                # each attempt's content equal to base + that attempt's edits.
                json={
                    "message": message,
                    "tree": tree["sha"],
                    "parents": [parent or base.commit],
                },
            )
            if existing is None:
                await client.post(
                    f"{self._repo(ref)}/git/refs",
                    json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
                )
            else:
                # `force` is left false. The new commit is parented on the tip read above, so
                # this is a fast-forward; if it were not, the check above already refused.
                await client.patch(
                    f"{self._repo(ref)}/git/refs/heads/{branch}",
                    json={"sha": commit["sha"], "force": False},
                )
        return BranchRef(name=branch, commit=str(commit["sha"]))

    async def _branch_tip(self, client: RestClient, ref: RepoRef, branch: str) -> BranchRef | None:
        """Where `branch` currently points, or `None` if it does not exist yet."""
        try:
            head: Any = await client.get(f"{self._repo(ref)}/git/ref/heads/{branch}")
        except NotFound:
            return None
        sha = head.get("object", {}).get("sha") if isinstance(head, dict) else None
        if not sha:
            raise AdapterError(f"branch {branch} exists but resolved to no commit")
        return BranchRef(name=branch, commit=str(sha))

    async def _commit_message(self, client: RestClient, ref: RepoRef, commit: str) -> str:
        """The message on `commit`. Read to decide whether a push has already landed."""
        payload: Any = await client.get(f"{self._repo(ref)}/git/commits/{commit}")
        return str(payload.get("message", "")) if isinstance(payload, dict) else ""

    async def delete_branch(self, ref: RepoRef, branch: str) -> bool:
        """`DELETE /git/refs/heads/{branch}`. A 404 means it is already gone."""
        try:
            async with await self._client(ref, CredentialKind.SCM_WRITE_BRANCH) as client:
                await client.delete(f"{self._repo(ref)}/git/refs/heads/{branch}")
        except NotFound:
            return False
        return True

    async def merge_pull_request(self, ref: RepoRef, number: str, commit: str) -> str:
        """PUT /repos/{org}/{repo}/pulls/{number}/merge — ADR 0004 model B's control point.

        `sha` is sent, and it is the whole point of the call. GitHub refuses the merge with
        409 if the pull request head has moved since that revision, which turns "merge what
        was verified" from an assumption into something the platform enforces. Without it a
        push landing between the verdict and the merge would be merged unverified, and every
        record in the audit trail would still look correct.

        Squash, because the branch is a history of the Coder's attempts (ADR 0007) and that
        is not a history the default branch should inherit.
        """
        try:
            async with await self._client(ref, CredentialKind.SCM_MERGE) as client:
                merged: Any = await client.put(
                    f"{self._repo(ref)}/pulls/{number}/merge",
                    json={"sha": commit, "merge_method": "squash"},
                )
        except PermissionDenied as exc:
            raise PermissionDenied(
                f"{exc}\n\nThis needs Contents: Read and write on "
                f"{ref.org}/{ref.repo}, and "
                "the branch protection rules on the target branch must admit this account."
            ) from None
        if not isinstance(merged, dict) or not merged.get("merged"):
            raise AdapterError(
                f"pull request {number} was not merged: "
                f"{merged.get('message') if isinstance(merged, dict) else merged!r}"
            )
        return str(merged.get("sha") or "")

    async def open_pull_request(
        self,
        ref: RepoRef,
        source: str,
        target: str,
        title: str,
        description: str,
    ) -> PullRequest:
        try:
            async with await self._client(ref, CredentialKind.SCM_PULL_REQUEST) as client:
                created: Any = await client.post(
                    f"{self._repo(ref)}/pulls",
                    json={"head": source, "base": target, "title": title, "body": description},
                )
        except PermissionDenied as exc:
            raise PermissionDenied(
                f"{exc}\n\nThis needs Pull requests: Read and write on "
                f"{ref.org}/{ref.repo}."
            ) from None
        if not isinstance(created, dict) or "number" not in created:
            raise AdapterError("pull request creation returned no number")
        number = str(created["number"])
        return PullRequest(
            id=number,
            url=str(
                created.get("html_url")
                or f"{self._web_url}/{ref.org}/{ref.repo}/pull/{number}"
            ),
            source_branch=source,
        )
