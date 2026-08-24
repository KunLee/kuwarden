"""Azure Repos.

`push_change` uses the Refs and Pushes APIs. No clone, no working tree, no git binary — which
matters because it means the Flow Engine can write a branch without ever materialising the
repository in a process that holds a credential.

Note what this class does not implement: merge, and deploy. Those are separate capabilities
under separate credentials, resolved after gates pass.
"""

from __future__ import annotations

from typing import Any

import httpx

from engine.adapters.credentials import (
    CredentialBroker,
    CredentialKind,
    CredentialRequest,
)
from engine.adapters.http import RestClient, basic_auth_header
from engine.adapters.protocols import (
    BranchRef,
    FileEdit,
    PullRequest,
    RepoRef,
    RepoTree,
    ScmCapabilities,
    TreeLimits,
)
from engine.adapters.scm.tree import check_file_count, check_file_size, excluded, finish
from engine.errors import AdapterError

API_VERSION = "7.1"

#: Azure DevOps' sentinel for "this ref does not exist yet", used when creating a branch.
EMPTY_OBJECT_ID = "0" * 40


class AzureReposScm:
    """Implements `ScmAdapter` for Azure Repos."""

    def __init__(
        self,
        broker: CredentialBroker,
        *,
        base_url: str = "https://dev.azure.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._broker = broker
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def _client(self, ref: RepoRef, kind: CredentialKind) -> RestClient:
        token = await self._broker.resolve(CredentialRequest(kind=kind, realm=ref.realm))
        return RestClient(
            base_url=f"{self._base_url}/{ref.org}",
            auth_header=basic_auth_header("", token),
            transport=self._transport,
        )

    def _repo_path(self, ref: RepoRef) -> str:
        if not ref.project:
            raise AdapterError("Azure Repos requires a project; RepoRef.project was not set")
        return f"/{ref.project}/_apis/git/repositories/{ref.repo}"

    async def probe(self, ref: RepoRef) -> ScmCapabilities:
        """Ask the platform what control points it actually offers — ADR 0004 §2.

        `restrictable_pipeline_triggers` is left false deliberately. Whether merging deploys
        depends on the pipeline's YAML triggers, which cannot be established from an API call
        with confidence. Model A therefore needs an explicit operator attestation rather than
        a probe result that would be a guess presented as a fact.
        """
        detail: dict[str, str] = {}

        deployment_protection = await self._endpoint_available(
            ref, f"/{ref.project}/_apis/pipelines/checks/configurations", detail, "checks"
        )
        required_status_checks = await self._endpoint_available(
            ref, f"/{ref.project}/_apis/policy/configurations", detail, "branch_policies"
        )
        detail["pipeline_triggers"] = (
            "not probed - requires operator attestation that merge does not deploy"
        )

        return ScmCapabilities(
            deployment_protection=deployment_protection,
            required_status_checks=required_status_checks,
            restrictable_pipeline_triggers=False,
            detail=detail,
        )

    async def _endpoint_available(
        self, ref: RepoRef, path: str, detail: dict[str, str], key: str
    ) -> bool:
        try:
            async with await self._client(ref, CredentialKind.SCM_READ) as client:
                await client.get(path, params={"api-version": API_VERSION})
        except AdapterError as exc:
            detail[key] = f"unavailable: {exc}"
            return False
        detail[key] = "available"
        return True

    async def default_branch(self, ref: RepoRef) -> BranchRef:
        async with await self._client(ref, CredentialKind.SCM_READ) as client:
            repo: Any = await client.get(
                self._repo_path(ref), params={"api-version": API_VERSION}
            )
            if not isinstance(repo, dict) or "defaultBranch" not in repo:
                raise AdapterError(f"repository {ref.repo} returned no default branch")
            full_ref = str(repo["defaultBranch"])
            name = full_ref.removeprefix("refs/heads/")

            refs: Any = await client.get(
                f"{self._repo_path(ref)}/refs",
                params={"api-version": API_VERSION, "filter": f"heads/{name}"},
            )
        entries = refs.get("value", []) if isinstance(refs, dict) else []
        if not entries:
            raise AdapterError(f"default branch {name} of {ref.repo} has no ref entry")
        return BranchRef(name=name, commit=str(entries[0]["objectId"]))

    async def write_access(self, ref: RepoRef) -> tuple[bool | None, str]:
        """Unknown, and said so.

        Azure DevOps exposes a PAT's scopes only through an endpoint the PAT itself may not
        read, so there is no cheap honest answer. `None` rather than `True`: a check that
        reports "writable" without checking is worse than no check, because it is trusted.
        """
        return None, "Azure DevOps exposes no cheap per-token permission read"

    async def read_tree(
        self, ref: RepoRef, commit: str, limits: TreeLimits | None = None
    ) -> RepoTree:
        """Every file at `commit`, via the Items API.

        Azure Repos returns content inline when asked, so this is one listing call plus one
        content call per file -- the same N+1 shape as GitHub, bounded the same way.
        """
        bounds = limits or TreeLimits()
        version = {
            "versionDescriptor.version": commit,
            "versionDescriptor.versionType": "commit",
        }

        async with await self._client(ref, CredentialKind.SCM_READ) as client:
            listing: Any = await client.get(
                f"{self._repo_path(ref)}/items",
                params={"api-version": API_VERSION, "recursionLevel": "full", **version},
            )
            entries = listing.get("value", []) if isinstance(listing, dict) else []

            wanted = [
                entry
                for entry in entries
                if isinstance(entry, dict)
                and not entry.get("isFolder")
                and not excluded(str(entry["path"]).lstrip("/"), bounds)
            ]
            check_file_count(len(wanted), bounds, f"{ref.org}/{ref.repo}")

            files: dict[str, bytes] = {}
            for entry in wanted:
                path = str(entry["path"]).lstrip("/")
                check_file_size(path, int(entry.get("size", 0)), bounds)
                content: Any = await client.request(
                    "GET",
                    f"{self._repo_path(ref)}/items",
                    params={
                        "api-version": API_VERSION,
                        "path": str(entry["path"]),
                        "includeContent": "true",
                        **version,
                    },
                )
                raw = content.get("content", "") if isinstance(content, dict) else ""
                files[path] = str(raw).encode()

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
        """Create or fast-forward `branch`. See `ScmAdapter.push_change` for the contract.

        **Known gap against that contract.** The Pushes API expresses a commit as changes
        relative to the branch tip, not as a tree, so a second push to the same branch is
        `tip + edits` rather than `base + edits`. A file this run changed in an earlier
        attempt and left alone in a later one therefore keeps the earlier content on the
        branch while being absent from the diff Build & Test graded. GitHub does not have this
        gap because the Git Data API takes an explicit `base_tree`. Closing it here needs the
        base content of every path the run has ever touched, which the state does not carry.
        """
        if not edits:
            raise AdapterError("refusing to push an empty change")

        tip = await self._branch_tip(ref, branch)
        if tip is not None:
            # The idempotency key -- a retried activity whose push landed must not commit
            # again. See the protocol docstring.
            if await self._commit_message(ref, tip.commit) == message:
                return tip
            expected = parent or base.commit
            if tip.commit != expected:
                raise AdapterError(
                    f"branch {branch} is at {tip.commit[:8]}, not the {expected[:8]} this "
                    "push was built on; refusing to overwrite work this run did not do"
                )
        else:
            # A push cannot create the branch and commit onto it in one call: the refUpdate's
            # `oldObjectId` must be the ref's current value, and for a ref that does not exist
            # that value is the zero id -- which produces a commit parented on nothing, losing
            # the entire history. So the ref is created at `base` first, then pushed onto.
            async with await self._client(ref, CredentialKind.SCM_WRITE_BRANCH) as client:
                await client.post(
                    f"{self._repo_path(ref)}/refs",
                    params={"api-version": API_VERSION},
                    json=[
                        {
                            "name": f"refs/heads/{branch}",
                            "oldObjectId": EMPTY_OBJECT_ID,
                            "newObjectId": base.commit,
                        }
                    ],
                )

        onto = tip.commit if tip is not None else base.commit
        existing = await self._file_paths(ref, onto)
        changes: list[dict[str, Any]] = []
        for edit in edits:
            if edit.deleted:
                # A delete carries no `newContent`; sending one is rejected. The path is not
                # checked against `existing` because a deletion of something already absent is
                # a bug worth hearing about from the API rather than silently dropping.
                changes.append(
                    {
                        "changeType": "delete",
                        "item": {"path": "/" + edit.path.lstrip("/")},
                    }
                )
                continue
            changes.append(
                {
                    # Azure DevOps rejects "add" for a path that exists and "edit" for one
                    # that does not, so the distinction has to be established before pushing.
                    "changeType": "edit" if edit.path.lstrip("/") in existing else "add",
                    "item": {"path": "/" + edit.path.lstrip("/")},
                    "newContent": {"content": edit.content, "contentType": "rawtext"},
                }
            )

        async with await self._client(ref, CredentialKind.SCM_WRITE_BRANCH) as client:
            result: Any = await client.post(
                f"{self._repo_path(ref)}/pushes",
                params={"api-version": API_VERSION},
                json={
                    "refUpdates": [{"name": f"refs/heads/{branch}", "oldObjectId": onto}],
                    "commits": [{"comment": message, "changes": changes}],
                },
            )
        commits = result.get("commits", []) if isinstance(result, dict) else []
        if not commits:
            raise AdapterError("push returned no commit")
        return BranchRef(name=branch, commit=str(commits[0]["commitId"]))

    async def _branch_tip(self, ref: RepoRef, branch: str) -> BranchRef | None:
        """Where `branch` currently points, or `None` if it does not exist yet.

        Azure answers a filter that matches nothing with an empty list and a 200, not a 404,
        so absence is read from the payload rather than from the status code.
        """
        async with await self._client(ref, CredentialKind.SCM_READ) as client:
            refs: Any = await client.get(
                f"{self._repo_path(ref)}/refs",
                params={"api-version": API_VERSION, "filter": f"heads/{branch}"},
            )
        entries = refs.get("value", []) if isinstance(refs, dict) else []
        if not entries:
            return None
        return BranchRef(name=branch, commit=str(entries[0]["objectId"]))

    async def _commit_message(self, ref: RepoRef, commit: str) -> str:
        """The message on `commit`. Read to decide whether a push has already landed."""
        async with await self._client(ref, CredentialKind.SCM_READ) as client:
            payload: Any = await client.get(
                f"{self._repo_path(ref)}/commits/{commit}",
                params={"api-version": API_VERSION},
            )
        return str(payload.get("comment", "")) if isinstance(payload, dict) else ""

    async def _file_paths(self, ref: RepoRef, commit: str) -> set[str]:
        """Every file present at `commit`, to decide `add` versus `edit` per change."""
        async with await self._client(ref, CredentialKind.SCM_READ) as client:
            items: Any = await client.get(
                f"{self._repo_path(ref)}/items",
                params={
                    "api-version": API_VERSION,
                    "recursionLevel": "full",
                    # By commit, not by branch name: after the first attempt the branch has
                    # moved, and the add/edit decision must reflect the commit being built on.
                    "versionDescriptor.version": commit,
                    "versionDescriptor.versionType": "commit",
                },
            )
        entries = items.get("value", []) if isinstance(items, dict) else []
        return {
            str(e["path"]).lstrip("/")
            for e in entries
            if isinstance(e, dict) and not e.get("isFolder")
        }

    async def delete_branch(self, ref: RepoRef, branch: str) -> bool:
        """Azure deletes a ref by updating it to the empty object id."""
        tip = await self._branch_tip(ref, branch)
        if tip is None:
            return False
        async with await self._client(ref, CredentialKind.SCM_WRITE_BRANCH) as client:
            await client.post(
                f"{self._repo_path(ref)}/refs",
                params={"api-version": API_VERSION},
                json=[
                    {
                        "name": f"refs/heads/{branch}",
                        "oldObjectId": tip.commit,
                        "newObjectId": EMPTY_OBJECT_ID,
                    }
                ],
            )
        return True

    async def merge_pull_request(self, ref: RepoRef, number: str, commit: str) -> str:
        """Not implemented, and refused rather than approximated.

        Completing a pull request on Azure Repos is a PATCH that sets `status: completed` with
        a `completionOptions` block, and its behaviour under branch policies differs from
        GitHub's in ways this codebase has not tested against a real organisation. ADR 0004
        model B is the control point: an implementation that merged under conditions nobody
        verified would be the one bug in this file that produces an unverified commit on a
        default branch while every audit row still reads correctly.

        Auto-merge therefore stays a GitHub capability until this is written and tested.
        """
        raise AdapterError(
            "merge_pull_request is not implemented for Azure Repos. Set "
            "delivery.auto_merge.enabled: false for this application, or merge the pull "
            "request by hand."
        )

    async def open_pull_request(
        self,
        ref: RepoRef,
        source: str,
        target: str,
        title: str,
        description: str,
    ) -> PullRequest:
        async with await self._client(ref, CredentialKind.SCM_PULL_REQUEST) as client:
            created: Any = await client.post(
                f"{self._repo_path(ref)}/pullrequests",
                params={"api-version": API_VERSION},
                json={
                    "sourceRefName": f"refs/heads/{source}",
                    "targetRefName": f"refs/heads/{target}",
                    "title": title,
                    "description": description,
                },
            )
        if not isinstance(created, dict) or "pullRequestId" not in created:
            raise AdapterError("pull request creation returned no id")
        pr_id = str(created["pullRequestId"])
        return PullRequest(
            id=pr_id,
            url=(
                f"{self._base_url}/{ref.org}/{ref.project}/_git/{ref.repo}"
                f"/pullrequest/{pr_id}"
            ),
            source_branch=source,
        )
