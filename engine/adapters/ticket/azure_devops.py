"""Azure DevOps Boards — work items as the system of record for work.

Everything `fetch` returns is hostile input. HTML is stripped from the description because
work-item descriptions are rich text and a model reading raw markup is a model reading more
attack surface than necessary — but stripping tags is tidying, not a security control. The
control is that this text never reaches anything holding a credential.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from engine.adapters.credentials import (
    CredentialBroker,
    CredentialKind,
    CredentialRequest,
)
from engine.adapters.http import RestClient, basic_auth_header
from engine.adapters.protocols import TicketRef
from engine.errors import AdapterError
from engine.state import Ticket

API_VERSION = "7.1"
COMMENTS_API_VERSION = "7.1-preview.3"

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t]*\n[ \t]*")


def _plain(html: str | None) -> str:
    if not html:
        return ""
    text = _TAG.sub(" ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return _WHITESPACE.sub("\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


class AzureDevOpsTickets:
    """Implements `TicketAdapter` for Azure DevOps Boards."""

    def __init__(
        self,
        organisation: str,
        broker: CredentialBroker,
        *,
        base_url: str = "https://dev.azure.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._org = organisation
        self._broker = broker
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def _client(self, ref: TicketRef) -> RestClient:
        token = await self._broker.resolve(
            CredentialRequest(kind=CredentialKind.TICKET_READ_WRITE, realm=ref.realm)
        )
        return RestClient(
            base_url=f"{self._base_url}/{self._org}",
            # Azure DevOps takes a PAT as the password with an empty username.
            auth_header=basic_auth_header("", token),
            transport=self._transport,
        )

    async def fetch(self, ref: TicketRef) -> Ticket:
        async with await self._client(ref) as client:
            body: Any = await client.get(
                f"/{ref.project}/_apis/wit/workitems/{ref.id}",
                params={"api-version": API_VERSION, "$expand": "all"},
            )
        if not isinstance(body, dict):
            raise AdapterError(f"unexpected work item payload for {ref.id}")

        fields: dict[str, Any] = body.get("fields", {})
        tags = fields.get("System.Tags") or ""
        points = fields.get("Microsoft.VSTS.Scheduling.StoryPoints")

        return Ticket(
            id=str(body.get("id", ref.id)),
            system="azure_devops",
            title=str(fields.get("System.Title", "")),
            body=_plain(fields.get("System.Description")),
            acceptance_criteria=_criteria(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria")),
            labels=[t.strip() for t in tags.split(";") if t.strip()],
            story_points=int(points) if isinstance(points, int | float) else None,
        )

    async def comment(self, ref: TicketRef, body: str) -> None:
        async with await self._client(ref) as client:
            await client.post(
                f"/{ref.project}/_apis/wit/workItems/{ref.id}/comments",
                params={"api-version": COMMENTS_API_VERSION},
                json={"text": body},
            )

    async def transition(self, ref: TicketRef, state: str) -> None:
        async with await self._client(ref) as client:
            await client.request(
                "PATCH",
                f"/{ref.project}/_apis/wit/workitems/{ref.id}",
                params={"api-version": API_VERSION},
                json=[{"op": "add", "path": "/fields/System.State", "value": state}],
                content_type="application/json-patch+json",
            )


def _criteria(raw: str | None) -> list[str]:
    text = _plain(raw)
    return [line.strip(" -*•\t") for line in text.splitlines() if line.strip()]
