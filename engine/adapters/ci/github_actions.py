"""GitHub Actions.

`GET /repos/{org}/{repo}/actions/runs?head_sha=...` — the workflow-runs listing, filtered by
commit on the server and filtered again by the caller, because a verdict about the wrong
revision is worse than no verdict at all.

Not the Checks API, which was the other candidate. Check runs include everything any app
posted against the commit — code scanners, coverage bots, a colleague's integration — and
KuWarden would be inferring which of those the organisation treats as gating. Workflow runs
are the repository's own pipeline definitions and nothing else, which is a set the application
can name in `kuwarden.yaml` and an operator can reason about.
"""

from __future__ import annotations

from typing import Any

import httpx

from engine.adapters.ci import CiRun
from engine.adapters.credentials import CredentialBroker, CredentialKind, CredentialRequest
from engine.adapters.http import RestClient, bearer_auth_header
from engine.adapters.protocols import RepoRef
from engine.errors import AdapterError

API_VERSION = "2022-11-28"

#: GitHub conclusions that count as a pass.
#:
#: `skipped` is a workflow whose conditions said it had nothing to do, and `neutral` is one
#: that explicitly declined to block — neither is a failure. Everything else counts as a
#: failure, including `cancelled`: a run somebody stopped is not evidence that anything
#: succeeded, and treating it as one would let a change through by interrupting its own check.
PASSING_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})


class GitHubActionsCi:
    """Implements `CiAdapter` for GitHub Actions.

    Holds `CI_READ` and nothing else. There is no method here that can start, cancel, or
    re-run a pipeline — see the `CiAdapter` docstring for why that absence is the point.
    """

    def __init__(
        self,
        broker: CredentialBroker,
        *,
        base_url: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._broker = broker
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def _client(self, ref: RepoRef) -> RestClient:
        token = await self._broker.resolve(
            CredentialRequest(kind=CredentialKind.CI_READ, realm=ref.realm)
        )
        return RestClient(
            base_url=self._base_url,
            auth_header=bearer_auth_header(token),
            transport=self._transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )

    async def runs_for(self, ref: RepoRef, commit: str) -> list[CiRun]:
        async with await self._client(ref) as client:
            payload: Any = await client.get(
                f"/repos/{ref.org}/{ref.repo}/actions/runs",
                # `per_page` is capped high deliberately. Paginating would mean deciding what
                # a partial answer means, and a partial answer here is a partial verdict.
                params={"head_sha": commit, "per_page": "100"},
            )
        if not isinstance(payload, dict):
            raise AdapterError(f"unexpected workflow-runs payload for {ref.org}/{ref.repo}")
        entries = payload.get("workflow_runs")
        if not isinstance(entries, list):
            raise AdapterError(f"workflow-runs payload for {commit[:8]} carried no run list")
        return [_run(entry) for entry in entries if isinstance(entry, dict)]


def _run(entry: dict[str, Any]) -> CiRun:
    """One workflow run, with GitHub's vocabulary normalised to pass / fail / pending.

    `status` and `conclusion` are separate fields and only the pair is meaningful: a run that
    has not completed carries `conclusion: null`, which is neither a pass nor a failure, and
    reading the conclusion alone would turn "still running" into "did not succeed".
    """
    completed = str(entry.get("status", "")) == "completed"
    conclusion = str(entry.get("conclusion") or "")
    return CiRun(
        id=str(entry.get("id", "")),
        name=str(entry.get("name") or "workflow"),
        workflow=str(entry.get("path") or ""),
        url=str(entry.get("html_url") or ""),
        head_sha=str(entry.get("head_sha") or ""),
        passed=(conclusion in PASSING_CONCLUSIONS) if completed else None,
        raw_conclusion=conclusion if completed else str(entry.get("status") or "unknown"),
    )
