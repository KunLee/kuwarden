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
    ScmCapabilities,
)
from engine.errors import AdapterError

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

        Branch protection returns 404 for a repository that has none *and* for a token
        without admin rights. Those are different facts and the API does not distinguish
        them, so the detail records the ambiguity rather than resolving it by guesswork.
        """
        detail: dict[str, str] = {}

        environments = await self._probe_endpoint(
            ref, f"{self._repo(ref)}/environments", detail, "environments"
        )
        default = await self.default_branch(ref)
        protection = await self._probe_endpoint(
            ref,
            f"{self._repo(ref)}/branches/{default.name}/protection",
            detail,
            "branch_protection",
        )
        if not protection:
            detail["branch_protection"] += (
                " - a 404 here means either no protection rule or a token without admin scope"
            )
        detail["workflow_triggers"] = (
            "not probed - requires operator attestation that merge does not deploy"
        )

        return ScmCapabilities(
            deployment_protection=environments,
            required_status_checks=protection,
            restrictable_pipeline_triggers=False,
            detail=detail,
        )

    async def _probe_endpoint(
        self, ref: RepoRef, path: str, detail: dict[str, str], key: str
    ) -> bool:
        try:
            async with await self._client(ref, CredentialKind.SCM_READ) as client:
                await client.get(path)
        except AdapterError as exc:
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

            head: Any = await client.get(f"{self._repo(ref)}/git/ref/heads/{name}")
        sha = head.get("object", {}).get("sha") if isinstance(head, dict) else None
        if not sha:
            raise AdapterError(f"branch {name} of {ref.repo} resolved to no commit")
        return BranchRef(name=name, commit=str(sha))

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

        async with await self._client(ref, CredentialKind.SCM_WRITE_BRANCH) as client:
            base_commit: Any = await client.get(f"{self._repo(ref)}/git/commits/{base.commit}")
            base_tree = (
                base_commit.get("tree", {}).get("sha")
                if isinstance(base_commit, dict)
                else None
            )
            if not base_tree:
                raise AdapterError(f"commit {base.commit} has no tree")

            tree_entries = []
            for edit in edits:
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
                json={"message": message, "tree": tree["sha"], "parents": [base.commit]},
            )
            await client.post(
                f"{self._repo(ref)}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
            )
        return BranchRef(name=branch, commit=str(commit["sha"]))

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
                f"{self._repo(ref)}/pulls",
                json={"head": source, "base": target, "title": title, "body": description},
            )
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
