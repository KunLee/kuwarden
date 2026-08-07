"""Azure Repos.

`push_change` uses the Pushes API, which creates a branch and a commit in one call. No clone,
no working tree, no git binary — which matters because it means the Flow Engine can write a
branch without ever materialising the repository in a process that holds a credential.

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
    ScmCapabilities,
)
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

    async def push_change(
        self,
        ref: RepoRef,
        base: BranchRef,
        branch: str,
        message: str,
        edits: list[FileEdit],
    ) -> BranchRef:
        if not edits:
            raise AdapterError("refusing to push an empty change")

        existing = await self._file_paths(ref, base)
        changes = [
            {
                # Azure DevOps rejects "add" for a path that exists and "edit" for one that
                # does not, so the distinction has to be established before pushing.
                "changeType": "edit" if edit.path.lstrip("/") in existing else "add",
                "item": {"path": "/" + edit.path.lstrip("/")},
                "newContent": {"content": edit.content, "contentType": "rawtext"},
            }
            for edit in edits
        ]

        async with await self._client(ref, CredentialKind.SCM_WRITE_BRANCH) as client:
            result: Any = await client.post(
                f"{self._repo_path(ref)}/pushes",
                params={"api-version": API_VERSION},
                json={
                    "refUpdates": [
                        {"name": f"refs/heads/{branch}", "oldObjectId": base.commit}
                    ],
                    "commits": [{"comment": message, "changes": changes}],
                },
            )
        commits = result.get("commits", []) if isinstance(result, dict) else []
        if not commits:
            raise AdapterError("push returned no commit")
        return BranchRef(name=branch, commit=str(commits[0]["commitId"]))

    async def _file_paths(self, ref: RepoRef, base: BranchRef) -> set[str]:
        async with await self._client(ref, CredentialKind.SCM_READ) as client:
            items: Any = await client.get(
                f"{self._repo_path(ref)}/items",
                params={
                    "api-version": API_VERSION,
                    "recursionLevel": "full",
                    "versionDescriptor.version": base.name,
                },
            )
        entries = items.get("value", []) if isinstance(items, dict) else []
        return {
            str(e["path"]).lstrip("/")
            for e in entries
            if isinstance(e, dict) and not e.get("isFolder")
        }

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
